from __future__ import annotations

import asyncio
from datetime import date
from typing import Any


class PermanentToolFailure(Exception):
    """Non-retriable tool failure (e.g. refund amount mismatch)."""


class ToolContext:
    """Simulated CRM / policy tools with optional transient fault injection for retries."""

    def __init__(
        self,
        dataset: dict[str, Any],
        *,
        simulate_faults: bool = False,
    ):
        self.customers_by_email = {c["email"].lower(): c for c in dataset["customers"]}
        self.customers_by_id = {c["customer_id"]: c for c in dataset["customers"]}
        self.orders_by_id = {o["order_id"]: o for o in dataset["orders"]}
        self.products_by_id = {p["product_id"]: p for p in dataset["products"]}
        self.knowledge_base = dataset["knowledge_base"]
        self._simulate_faults = simulate_faults
        self._fault_get_order_once = simulate_faults
        self._fault_kb_once = simulate_faults
        self._fault_lock = asyncio.Lock()
        self._order_lock = asyncio.Lock()

    async def begin_ticket_session(
        self,
        ticket_id: str,
        *,
        intent: str,
        confidence: float,
        urgency: str,
        category: str,
        resolvability: str,
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        if not ticket_id:
            raise ValueError("Missing ticket_id")
        return {
            "status": "ready",
            "ticket_id": ticket_id,
            "intent": intent,
            "confidence": round(confidence, 2),
            "urgency": urgency,
            "category": category,
            "resolvability": resolvability,
        }

    async def get_customer(self, email: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        if not email:
            raise ValueError("Missing email")
        out = self.customers_by_email.get(email.lower())
        return out if isinstance(out, dict) else None

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        async with self._fault_lock:
            if self._fault_get_order_once:
                self._fault_get_order_once = False
                raise TimeoutError("simulated transient outage on order service")
        if not order_id:
            raise ValueError("Missing order_id")
        out = self.orders_by_id.get(order_id.upper())
        return out if isinstance(out, dict) else None

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        if not product_id:
            raise ValueError("Missing product_id")
        out = self.products_by_id.get(product_id.upper())
        return out if isinstance(out, dict) else None

    def _evaluate_refund_eligibility(
        self,
        customer: dict[str, Any] | None,
        order: dict[str, Any] | None,
        product: dict[str, Any] | None,
        message: str,
    ) -> dict[str, Any]:
        if not isinstance(message, str):
            raise ValueError("Malformed message")
        if not order or not product:
            return {"eligible": False, "reason": "Missing order or product details"}
        if order.get("refund_status") == "refunded":
            return {"eligible": False, "reason": "Already refunded"}

        msg = message.lower()
        notes = (order.get("notes") or "").lower()
        customer_notes = (customer or {}).get("notes", "").lower()
        delivered = order.get("delivery_date")
        deadline = order.get("return_deadline")

        if "damaged" in msg or "defect" in msg or "stopped working" in msg or "cracked" in msg:
            if "replacement" in msg:
                return {"eligible": False, "reason": "Customer requested replacement"}
            return {"eligible": True, "reason": "Damaged/defective policy"}
        if "wrong size" in msg or "wrong colour" in msg or "wrong color" in msg or "wrong item" in msg:
            return {"eligible": True, "reason": "Wrong item delivered"}
        if order.get("status") == "processing":
            return {"eligible": True, "reason": "Order can be canceled pre-shipment"}
        if "registered online" in notes:
            return {"eligible": False, "reason": "Registered device is non-returnable"}
        if deadline and delivered:
            if date.fromisoformat(deadline) >= date(2024, 3, 15):
                return {"eligible": True, "reason": "Within return window"}
            if "extended return exception" in customer_notes:
                return {"eligible": True, "reason": "VIP exception"}
        return {"eligible": False, "reason": "Outside return policy"}

    async def check_refund_eligibility(self, order_id: str, *, message: str = "") -> dict[str, Any]:
        """PDF: check_refund_eligibility(order_id). Ticket text supplied as message for policy rules."""
        await asyncio.sleep(0)
        oid = (order_id or "").strip().upper()
        if not oid:
            raise ValueError("Missing order_id")
        order = self.orders_by_id.get(oid)
        if not order:
            raise ValueError(f"Unknown order_id: {order_id}")
        cid = order.get("customer_id")
        customer = self.customers_by_id.get(cid) if cid else None
        pid = order.get("product_id")
        product = self.products_by_id.get(pid) if pid else None
        return self._evaluate_refund_eligibility(customer, order, product, message)

    async def issue_refund(self, order_id: str, amount: float) -> dict[str, Any]:
        """PDF: issue_refund(order_id, amount). IRREVERSIBLE — amount must match the order record."""
        async with self._order_lock:
            await asyncio.sleep(0)
            oid = (order_id or "").strip().upper()
            order = self.orders_by_id.get(oid)
            if not order:
                raise ValueError("Order not found")
            expected = float(order.get("amount") or 0)
            if abs(float(amount) - expected) > 0.009:
                raise PermanentToolFailure(
                    f"Refund amount {amount} does not match order total {expected}"
                )
            if order.get("refund_status") == "refunded":
                return {"status": "already_refunded", "amount": expected}
            order["refund_status"] = "refunded"
            return {"status": "refunded", "order_id": oid, "amount": expected}

    async def search_knowledge_base(self, query: str) -> dict[str, str]:
        await asyncio.sleep(0)
        async with self._fault_lock:
            if self._fault_kb_once:
                self._fault_kb_once = False
                # Malformed partial response — missing required fields for schema validation.
                return {"snippet": "partial"}
        if not isinstance(query, str):
            raise ValueError("Malformed KB query")
        q = query.lower()
        if "return" in q:
            return {"answer": "Most products have a 30-day return window; some categories differ."}
        if "refund" in q:
            return {"answer": "Refunds are processed in 5-7 business days after approval."}
        if "exchange" in q:
            return {"answer": "Exchanges are available for wrong size/colour/item, subject to stock."}
        return {"answer": "Please share more details so we can help precisely."}

    async def send_reply(self, ticket_id: str, message: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        if not ticket_id or not message:
            raise ValueError("Missing ticket reply fields")
        return {"ticket_id": ticket_id, "status": "sent"}

    async def escalate(self, ticket_id: str, summary: dict[str, Any], priority: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        if not ticket_id:
            raise ValueError("Missing ticket_id")
        if not isinstance(summary, dict):
            raise ValueError("Malformed escalation summary")
        p = (priority or "medium").strip().lower()
        if p not in {"high", "medium", "low"}:
            raise ValueError("Invalid priority")
        return {"ticket_id": ticket_id, "status": "escalated", "summary": summary, "priority": p}
