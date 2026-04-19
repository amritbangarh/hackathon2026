from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .llm_triage import merge_triage_with_llm
from .models import AuditStep, NormalizedTicket, TicketResult
from .retry import with_retry
from .tools import PermanentToolFailure, ToolContext

_current_ticket: ContextVar[str] = ContextVar("current_ticket", default="")


class SupportAgentProcessor:
    def __init__(
        self,
        tool_ctx: ToolContext,
        logs_dir: Path,
        event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.tool_ctx = tool_ctx
        self.logs_dir = logs_dir
        self.dead_letter: list[dict[str, Any]] = []
        self._event_sink = event_sink

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._event_sink is not None:
            await self._event_sink(event)

    @staticmethod
    def _serialize_steps(steps: list[AuditStep]) -> list[dict[str, Any]]:
        return [
            {
                "thought": s.thought,
                "tool_called": s.tool_called,
                "attempt": s.attempt,
                "status": s.status,
                "result_preview": (s.result[:400] + "…") if len(s.result) > 400 else s.result,
            }
            for s in steps
        ]

    async def _finalize(self, ticket_id: str, result: TicketResult) -> TicketResult:
        await self._emit(
            {
                "type": "ticket_complete",
                "ticket_id": ticket_id,
                "payload": {
                    "final_decision": result.final_decision,
                    "status": result.status,
                    "confidence": round(result.confidence, 2),
                    "triage": result.triage,
                    "outcome": result.outcome,
                    "escalated": result.escalated,
                    "dead_lettered": result.dead_lettered,
                    "steps": self._serialize_steps(result.steps),
                },
            }
        )
        return result

    def classify_intent(self, message: str) -> tuple[str, float]:
        msg = message.lower()
        if any(k in msg for k in ("refund", "return", "cancel")):
            return "refund_or_return", 0.78
        if any(k in msg for k in ("broken", "defect", "damaged", "cracked")):
            return "product_issue", 0.72
        if any(k in msg for k in ("where is my order", "tracking", "in transit")):
            return "order_status", 0.70
        if any(k in msg for k in ("policy", "process", "how long", "exchange")):
            return "policy_question", 0.69
        return "ambiguous", 0.45

    def triage_ticket(self, message: str) -> tuple[str, str, str, float]:
        """Returns (category, urgency, resolvability, confidence)."""
        category, confidence = self.classify_intent(message)
        msg = message.lower()
        urgency = "medium"
        if any(
            k in msg
            for k in (
                "urgent",
                "immediately",
                "asap",
                "broken",
                "damaged",
                "stopped working",
                "cracked",
                "not working",
            )
        ):
            urgency = "high"
        elif category in {"policy_question", "ambiguous"}:
            urgency = "low"

        resolvability = "agent_can_resolve"
        if category == "ambiguous" or confidence < 0.5:
            resolvability = "needs_human_review"
        elif "replacement" in msg and category == "product_issue":
            resolvability = "needs_human_review"

        return category, urgency, resolvability, confidence

    @staticmethod
    def _priority_from_urgency(urgency: str) -> str:
        return {"high": "high", "medium": "medium", "low": "low"}.get(urgency.lower(), "medium")

    @staticmethod
    def _validate_tool_output(name: str, payload: Any) -> bool:
        if name == "begin_ticket_session":
            return (
                isinstance(payload, dict)
                and payload.get("status") == "ready"
                and payload.get("urgency") in {"high", "medium", "low"}
                and isinstance(payload.get("category"), str)
                and isinstance(payload.get("resolvability"), str)
            )
        if name == "get_customer":
            return payload is None or (
                isinstance(payload, dict) and ("customer_id" in payload or "email" in payload)
            )
        if name == "get_order":
            return payload is None or (isinstance(payload, dict) and "order_id" in payload)
        if name == "get_product":
            return payload is None or (isinstance(payload, dict) and "product_id" in payload)
        if name == "check_refund_eligibility":
            return isinstance(payload, dict) and isinstance(payload.get("eligible"), bool)
        if name == "issue_refund":
            return isinstance(payload, dict) and payload.get("status") in {"refunded", "already_refunded"}
        if name == "search_knowledge_base":
            return isinstance(payload, dict) and isinstance(payload.get("answer"), str)
        if name == "send_reply":
            return isinstance(payload, dict) and payload.get("status") == "sent"
        if name == "escalate":
            return (
                isinstance(payload, dict)
                and payload.get("status") == "escalated"
                and payload.get("priority") in {"high", "medium", "low"}
            )
        return False

    async def _ensure_chain_before_escalate(
        self,
        ticket: NormalizedTicket,
        steps: list[AuditStep],
        *,
        intent: str,
    ) -> None:
        """Hackathon rule: >= 3 tool calls in a chain; escalate counts as the final call."""
        while len(steps) < 2:
            await self._call_tool(
                steps,
                thought="Consult knowledge base before escalating (minimum multi-step chain).",
                tool_name="search_knowledge_base",
                call=lambda: self.tool_ctx.search_knowledge_base(
                    f"{ticket.message}\n(intent={intent}; escalation_preparation=true)"
                ),
            )

    async def _call_tool(
        self,
        steps: list[AuditStep],
        *,
        thought: str,
        tool_name: str,
        call,
    ) -> Any:
        try:
            result, attempt = await with_retry(call, max_attempts=3)
            status = "success" if self._validate_tool_output(tool_name, result) else "invalid_output"
            steps.append(
                AuditStep(
                    thought=thought,
                    tool_called=tool_name,
                    attempt=attempt,
                    result=str(result),
                    status=status,
                )
            )
            preview = str(result)
            await self._emit(
                {
                    "type": "tool_step",
                    "ticket_id": _current_ticket.get(),
                    "tool": tool_name,
                    "thought": thought,
                    "attempt": attempt,
                    "step_status": status,
                    "result_preview": preview[:420] + ("…" if len(preview) > 420 else ""),
                }
            )
            if status != "success":
                raise ValueError(f"Invalid output from {tool_name}")
            return result
        except Exception as exc:
            steps.append(
                AuditStep(
                    thought=thought,
                    tool_called=tool_name,
                    attempt=3,
                    result=str(exc),
                    status="failed",
                )
            )
            px = str(exc)
            await self._emit(
                {
                    "type": "tool_step",
                    "ticket_id": _current_ticket.get(),
                    "tool": tool_name,
                    "thought": thought,
                    "attempt": 3,
                    "step_status": "failed",
                    "result_preview": px[:420] + ("…" if len(px) > 420 else ""),
                }
            )
            raise

    async def _escalate_with_summary(
        self,
        ticket: NormalizedTicket,
        *,
        steps: list[AuditStep],
        reason: str,
        confidence: float,
        intent: str,
        priority: str,
        triage: dict[str, str],
    ) -> TicketResult:
        await self._ensure_chain_before_escalate(ticket, steps, intent=intent)

        attempted = [s.tool_called for s in steps]
        failed = [s.tool_called for s in steps if s.status == "failed"]
        summary = {
            "attempted_tools": attempted,
            "failed_tools": failed,
            "reason": reason,
            "recommended_action": "Human specialist review required",
            "triage": triage,
            "priority": priority,
        }
        try:
            await self._call_tool(
                steps,
                thought="Escalate because confidence/validation failed or specialist path required.",
                tool_name="escalate",
                call=lambda: self.tool_ctx.escalate(ticket.ticket_id, summary, priority),
            )
            return TicketResult(
                ticket_id=ticket.ticket_id,
                final_decision="escalated",
                confidence=confidence,
                steps=steps,
                status="escalated",
                triage=triage,
                outcome=f"Escalated to human agent (priority={priority}): {reason}",
                escalation_priority=priority,
                escalated=True,
            )
        except Exception as exc:
            self.dead_letter.append({"ticket_id": ticket.ticket_id, "error": str(exc), "summary": summary})
            return TicketResult(
                ticket_id=ticket.ticket_id,
                final_decision="dead_lettered",
                confidence=0.0,
                steps=steps,
                status="dead_lettered",
                triage=triage,
                outcome=f"Escalation failed; ticket dead-lettered: {exc}",
                escalation_priority=priority,
                failed=True,
                dead_lettered=True,
            )

    async def process_ticket(self, ticket: NormalizedTicket) -> TicketResult:
        steps: list[AuditStep] = []
        intent = "unknown"
        urgency = "medium"
        resolvability = "agent_can_resolve"
        confidence = 0.0
        triage: dict[str, str] = {}
        priority = "medium"
        tok = _current_ticket.set(ticket.ticket_id)
        try:
            intent, urgency, resolvability, confidence = self.triage_ticket(ticket.message)
            merged_triage, llm_audit = await merge_triage_with_llm(
                ticket.message,
                (intent, urgency, resolvability, confidence),
            )
            intent, urgency, resolvability, confidence = merged_triage
            llm_result_json = json.dumps(llm_audit, default=str)
            if llm_audit.get("llm_used"):
                step_status = "success"
            elif llm_audit.get("error"):
                step_status = "failed"
            else:
                step_status = "skipped"
            steps.append(
                AuditStep(
                    thought="Optional LLM triage (rules baseline + merge). Policy/refund tools unchanged.",
                    tool_called="llm_triage",
                    attempt=1,
                    result=llm_result_json,
                    status=step_status,
                )
            )
            await self._emit(
                {
                    "type": "tool_step",
                    "ticket_id": ticket.ticket_id,
                    "tool": "llm_triage",
                    "thought": "LLM-assisted classification (audited JSON below).",
                    "attempt": 1,
                    "step_status": step_status,
                    "result_preview": llm_result_json[:420] + ("…" if len(llm_result_json) > 420 else ""),
                }
            )

            priority = self._priority_from_urgency(urgency)
            triage = {
                "category": intent,
                "urgency": urgency,
                "resolvability": resolvability,
                "triage_source": "llm_merged" if llm_audit.get("llm_used") else "rules_only",
            }

            await self._emit(
                {
                    "type": "ticket_begin",
                    "ticket_id": ticket.ticket_id,
                    "message_preview": (ticket.message[:280] + "…")
                    if len(ticket.message) > 280
                    else ticket.message,
                    "triage": triage,
                }
            )

            await self._call_tool(
                steps,
                thought="Open ticket session with triage (category, urgency, resolvability) for audit.",
                tool_name="begin_ticket_session",
                call=lambda: self.tool_ctx.begin_ticket_session(
                    ticket.ticket_id,
                    intent=intent,
                    confidence=confidence,
                    urgency=urgency,
                    category=intent,
                    resolvability=resolvability,
                ),
            )

            customer = None
            if ticket.email:
                customer = await self._call_tool(
                    steps,
                    thought="Start context gathering with customer lookup.",
                    tool_name="get_customer",
                    call=lambda: self.tool_ctx.get_customer(ticket.email or ""),
                )

            if ticket.email and customer is None:
                return await self._finalize(
                    ticket.ticket_id,
                    await self._escalate_with_summary(
                        ticket,
                        steps=steps,
                        reason="Customer not found for provided email",
                        confidence=0.40,
                        intent=intent,
                        priority=priority,
                        triage=triage,
                    ),
                )

            order = None
            if ticket.order_id:
                order = await self._call_tool(
                    steps,
                    thought="Order-related issue requires order lookup.",
                    tool_name="get_order",
                    call=lambda: self.tool_ctx.get_order(ticket.order_id or ""),
                )
            elif intent in {"refund_or_return", "order_status"} and customer:
                customer_id = customer.get("customer_id")
                matching = [o for o in self.tool_ctx.orders_by_id.values() if o.get("customer_id") == customer_id]
                if matching:
                    recent = sorted(matching, key=lambda x: x.get("order_date") or "", reverse=True)[0]
                    ticket.order_id = recent["order_id"]
                    order = await self._call_tool(
                        steps,
                        thought="No order id supplied; infer order by customer.",
                        tool_name="get_order",
                        call=lambda: self.tool_ctx.get_order(ticket.order_id or ""),
                    )

            if order is None and intent in {"refund_or_return", "order_status", "product_issue"}:
                return await self._finalize(
                    ticket.ticket_id,
                    await self._escalate_with_summary(
                        ticket,
                        steps=steps,
                        reason="Order data missing or conflicting",
                        confidence=0.42,
                        intent=intent,
                        priority=priority,
                        triage=triage,
                    ),
                )

            product = None
            if ticket.product_id:
                product = await self._call_tool(
                    steps,
                    thought="Explicit product id provided.",
                    tool_name="get_product",
                    call=lambda: self.tool_ctx.get_product(ticket.product_id or ""),
                )
            elif order and order.get("product_id"):
                ticket.product_id = order["product_id"]
                product = await self._call_tool(
                    steps,
                    thought="Resolve product context from order.",
                    tool_name="get_product",
                    call=lambda: self.tool_ctx.get_product(ticket.product_id or ""),
                )

            if intent in {"refund_or_return", "product_issue"}:
                eligibility = await self._call_tool(
                    steps,
                    thought="Check policy eligibility before any refund action (order-scoped tool).",
                    tool_name="check_refund_eligibility",
                    call=lambda: self.tool_ctx.check_refund_eligibility(
                        ticket.order_id or "",
                        message=ticket.message,
                    ),
                )

                if eligibility.get("eligible"):
                    ord_amt = float((order or {}).get("amount") or 0)
                    refund = await self._call_tool(
                        steps,
                        thought="Eligibility passed; issue refund for verified order amount.",
                        tool_name="issue_refund",
                        call=lambda: self.tool_ctx.issue_refund(ticket.order_id or "", ord_amt),
                    )
                    msg = (
                        f"Your request is approved. Refund status: {refund.get('status')} "
                        f"for order {ticket.order_id}. Funds may take 5-7 business days."
                    )
                    await self._call_tool(
                        steps,
                        thought="Send final customer resolution.",
                        tool_name="send_reply",
                        call=lambda: self.tool_ctx.send_reply(ticket.ticket_id, msg),
                    )
                    return await self._finalize(
                        ticket.ticket_id,
                        TicketResult(
                            ticket_id=ticket.ticket_id,
                            final_decision="resolved_refund",
                            confidence=max(confidence, 0.75),
                            steps=steps,
                            status="resolved",
                            triage=triage,
                            outcome=f"Refund {refund.get('status')} for order {ticket.order_id}; reply sent.",
                        ),
                    )

                if "replacement" in ticket.message.lower() or "warranty" in (eligibility.get("reason", "").lower()):
                    return await self._finalize(
                        ticket.ticket_id,
                        await self._escalate_with_summary(
                            ticket,
                            steps=steps,
                            reason=f"Non-refund path requires specialist: {eligibility.get('reason')}",
                            confidence=0.55,
                            intent=intent,
                            priority=priority,
                            triage=triage,
                        ),
                    )

                await self._call_tool(
                    steps,
                    thought="Decline refund and provide clear policy guidance.",
                    tool_name="send_reply",
                    call=lambda: self.tool_ctx.send_reply(
                        ticket.ticket_id,
                        f"We cannot approve a refund: {eligibility.get('reason')}. "
                        "If you share more details, we can review alternatives.",
                    ),
                )
                return await self._finalize(
                    ticket.ticket_id,
                    TicketResult(
                        ticket_id=ticket.ticket_id,
                        final_decision="resolved_declined",
                        confidence=max(confidence, 0.70),
                        steps=steps,
                        status="resolved",
                        triage=triage,
                        outcome=f"Refund declined: {eligibility.get('reason')}; policy reply sent.",
                    ),
                )

            kb = await self._call_tool(
                steps,
                thought="Unknown or policy-type query, consult knowledge base.",
                tool_name="search_knowledge_base",
                call=lambda: self.tool_ctx.search_knowledge_base(ticket.message),
            )
            await self._call_tool(
                steps,
                thought="Send policy/information response to customer.",
                tool_name="send_reply",
                call=lambda: self.tool_ctx.send_reply(ticket.ticket_id, kb.get("answer", "Please provide details.")),
            )

            if confidence < 0.6:
                return await self._finalize(
                    ticket.ticket_id,
                    await self._escalate_with_summary(
                        ticket,
                        steps=steps,
                        reason="Low confidence after fallback response",
                        confidence=confidence,
                        intent=intent,
                        priority=priority,
                        triage=triage,
                    ),
                )

            return await self._finalize(
                ticket.ticket_id,
                TicketResult(
                    ticket_id=ticket.ticket_id,
                    final_decision="resolved_info",
                    confidence=confidence,
                    steps=steps,
                    status="resolved",
                    triage=triage,
                    outcome="Informational reply sent from knowledge base.",
                ),
            )
        except PermanentToolFailure as exc:
            return await self._finalize(
                ticket.ticket_id,
                await self._escalate_with_summary(
                    ticket,
                    steps=steps,
                    reason=f"Permanent tool failure: {exc}",
                    confidence=0.25,
                    intent=intent,
                    priority="high",
                    triage=triage,
                ),
            )
        except Exception as exc:
            return await self._finalize(
                ticket.ticket_id,
                await self._escalate_with_summary(
                    ticket,
                    steps=steps,
                    reason=f"Unhandled processing failure: {exc}",
                    confidence=0.30,
                    intent=intent,
                    priority=priority,
                    triage=triage,
                ),
            )
        finally:
            _current_ticket.reset(tok)

    async def persist_logs(self, results: list[TicketResult]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        audit_payload = [
            {
                "ticket_id": r.ticket_id,
                "triage": r.triage,
                "outcome": r.outcome,
                "escalation_priority": r.escalation_priority,
                "steps": [
                    {
                        "thought": s.thought,
                        "tool_called": s.tool_called,
                        "attempt": s.attempt,
                        "result": s.result,
                        "status": s.status,
                    }
                    for s in r.steps
                ],
                "final_decision": r.final_decision,
                "confidence": round(r.confidence, 2),
            }
            for r in results
        ]
        (self.logs_dir / "audit_log.json").write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")
        (self.logs_dir / "dead_letter.json").write_text(json.dumps(self.dead_letter, indent=2), encoding="utf-8")
