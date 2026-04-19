from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

BaselineTriage = tuple[str, str, str, float]

VALID_CATEGORY = frozenset(
    {
        "refund_or_return",
        "product_issue",
        "order_status",
        "policy_question",
        "ambiguous",
    }
)
VALID_URGENCY = frozenset({"high", "medium", "low"})
VALID_RESOLVABILITY = frozenset({"agent_can_resolve", "needs_human_review"})


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def llm_triage_enabled() -> bool:
    return _env_truthy("SHOPWAVE_USE_LLM_TRIAGE")


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    # ```json ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # try first { ... } block
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def _ollama_content_to_audit_and_parsed(content: Any) -> tuple[str, dict[str, Any] | None]:
    """Ollama may return JSON `content` as a string or as an already-parsed object."""
    if isinstance(content, dict):
        try:
            text = json.dumps(content)
        except (TypeError, ValueError):
            text = str(content)
        return text[:4000], content
    if isinstance(content, str):
        return content[:4000], None
    if content is None:
        return "", None
    return str(content)[:4000], None


def _normalize_parsed(raw: dict[str, Any]) -> tuple[str, str, str, float] | None:
    cat = str(raw.get("category", "")).strip()
    urg = str(raw.get("urgency", "")).strip().lower()
    res = str(raw.get("resolvability", "")).strip().lower()
    conf_v = raw.get("confidence")
    try:
        conf = float(conf_v)
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(1.0, conf))

    if cat not in VALID_CATEGORY:
        return None
    if urg not in VALID_URGENCY:
        return None
    if res not in VALID_RESOLVABILITY:
        return None
    return cat, urg, res, conf


def _merge_conservative(baseline: BaselineTriage, llm: tuple[str, str, str, float]) -> BaselineTriage:
    """Prefer LLM when confident; always escalate resolvability if either side says human review."""
    cat_r, urg_r, res_r, conf_r = baseline
    c, u, res, cf = llm
    res_out = (
        "needs_human_review"
        if res_r == "needs_human_review" or res == "needs_human_review"
        else res
    )
    if cf >= 0.55:
        return (c, u, res_out, max(cf, conf_r))
    return (cat_r, urg_r, res_out, conf_r)


SYSTEM_PROMPT = """You are a support ticket triage model for ShopWave e-commerce.
Return ONLY a JSON object (no markdown) with keys:
- category: one of refund_or_return, product_issue, order_status, policy_question, ambiguous
- urgency: one of high, medium, low
- resolvability: agent_can_resolve OR needs_human_review (use needs_human_review for warranty/replacement/legal ambiguity)
- confidence: number 0.0-1.0
- reasoning: one short sentence (will be audited)

Do not invent order numbers or policies; only classify the customer message."""


async def merge_triage_with_llm(
    customer_message: str,
    baseline: BaselineTriage,
) -> tuple[BaselineTriage, dict[str, Any]]:
    """
    Optionally call OpenAI or Ollama; merge conservatively with rule-based baseline.
    Second return value is always logged to audit (explainability).
    """
    audit: dict[str, Any] = {
        "backend": None,
        "baseline": {
            "category": baseline[0],
            "urgency": baseline[1],
            "resolvability": baseline[2],
            "confidence": baseline[3],
        },
        "llm_used": False,
    }
    if not llm_triage_enabled():
        audit["skip_reason"] = "SHOPWAVE_USE_LLM_TRIAGE not enabled"
        return baseline, audit

    ollama = os.environ.get("OLLAMA_BASE_URL", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if ollama:
        model = os.environ.get("OLLAMA_MODEL", "llama3.2").strip()
        base = ollama.rstrip("/")
        url = f"{base}/api/chat"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Customer message:\n{customer_message}",
                },
            ],
            "stream": False,
            "format": "json",
        }
        audit["backend"] = "ollama"
        audit["model"] = model
        audit["request_endpoint"] = url
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
            msg = data.get("message") or {}
            raw_content = msg.get("content") if isinstance(msg, dict) else None
            audit_text, parsed_direct = _ollama_content_to_audit_and_parsed(raw_content)
            if not audit_text and parsed_direct is None:
                audit["error"] = "unexpected ollama response shape (empty content)"
                return baseline, audit
            audit["raw_text"] = audit_text
            parsed = parsed_direct if isinstance(parsed_direct, dict) else _extract_json(audit_text)
            if not parsed:
                audit["error"] = "could not parse JSON from Ollama"
                return baseline, audit
            norm = _normalize_parsed(parsed)
            if not norm:
                audit["error"] = "invalid enum values in Ollama JSON"
                audit["parsed_raw"] = parsed
                return baseline, audit
            merged = _merge_conservative(baseline, norm)
            audit["llm_used"] = True
            audit["llm_parsed"] = {
                "category": norm[0],
                "urgency": norm[1],
                "resolvability": norm[2],
                "confidence": norm[3],
                "reasoning": str(parsed.get("reasoning", ""))[:500],
            }
            audit["merged"] = {
                "category": merged[0],
                "urgency": merged[1],
                "resolvability": merged[2],
                "confidence": merged[3],
            }
            audit["merge_rule"] = "conservative: LLM fields if confidence>=0.55 else baseline category/urgency; resolvability union"
            return merged, audit

        except Exception as exc:
            audit["error"] = f"ollama_request_failed: {exc}"
            return baseline, audit

    if openai_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Customer message:\n{customer_message}"},
            ],
            "temperature": 0.2,
        }
        # JSON mode when supported (多数 chat models on OpenAI API)
        body["response_format"] = {"type": "json_object"}
        audit["backend"] = "openai"
        audit["model"] = model
        audit["request_endpoint"] = url
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(url, json=body, headers=headers)
                if r.status_code == 400 and "response_format" in body:
                    del body["response_format"]
                    r = await client.post(url, json=body, headers=headers)
                txt = r.text
                if r.status_code >= 400:
                    audit["error"] = f"openai_http_{r.status_code}: {txt[:800]}"
                    return baseline, audit
                data = r.json()
            choice0 = (data.get("choices") or [{}])[0]
            msg = choice0.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, str):
                audit["error"] = "unexpected openai response shape"
                return baseline, audit
            audit["raw_text"] = content[:4000]
            parsed = _extract_json(content)
            if not parsed:
                audit["error"] = "could not parse JSON from OpenAI"
                return baseline, audit
            norm = _normalize_parsed(parsed)
            if not norm:
                audit["error"] = "invalid enum values in OpenAI JSON"
                audit["parsed_raw"] = parsed
                return baseline, audit
            merged = _merge_conservative(baseline, norm)
            audit["llm_used"] = True
            audit["llm_parsed"] = {
                "category": norm[0],
                "urgency": norm[1],
                "resolvability": norm[2],
                "confidence": norm[3],
                "reasoning": str(parsed.get("reasoning", ""))[:500],
            }
            audit["merged"] = {
                "category": merged[0],
                "urgency": merged[1],
                "resolvability": merged[2],
                "confidence": merged[3],
            }
            audit["merge_rule"] = "conservative: LLM fields if confidence>=0.55 else baseline category/urgency; resolvability union"
            return merged, audit
        except Exception as exc:
            audit["error"] = f"openai_request_failed: {exc}"
            return baseline, audit

    audit["skip_reason"] = "Set OLLAMA_BASE_URL or OPENAI_API_KEY"
    return baseline, audit
