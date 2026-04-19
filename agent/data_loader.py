from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import NormalizedTicket

ORDER_ID_RE = re.compile(r"\bORD-\d{4}\b", re.IGNORECASE)


def _load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {path}")
    return data


def locate_dataset_dir(root: Path) -> Path:
    matches = sorted(root.glob("agentic_ai_hackthon_2026_sample_data-main"))
    if not matches:
        raise FileNotFoundError("Dataset directory not found.")
    return matches[0]


def load_dataset(root: Path) -> dict[str, Any]:
    dataset_dir = locate_dataset_dir(root)
    tickets = _load_json(dataset_dir / "tickets.json")
    customers = _load_json(dataset_dir / "customers.json")
    orders = _load_json(dataset_dir / "orders.json")
    products = _load_json(dataset_dir / "products.json")
    kb_text = (dataset_dir / "knowledge-base.md").read_text(encoding="utf-8")

    return {
        "tickets": tickets,
        "customers": customers,
        "orders": orders,
        "products": products,
        "knowledge_base": kb_text,
    }


def normalize_ticket(raw: dict[str, Any]) -> NormalizedTicket:
    ticket_id = str(raw.get("ticket_id") or "UNKNOWN")
    subject = str(raw.get("subject") or "").strip()
    body = str(raw.get("body") or "").strip()
    message = f"{subject}\n{body}".strip() or "No message provided."
    email = raw.get("customer_email")
    email = str(email).strip().lower() if email else None

    order_id = None
    body_match = ORDER_ID_RE.search(body)
    if body_match:
        order_id = body_match.group(0).upper()

    product_id = raw.get("product_id")
    product_id = str(product_id).strip().upper() if product_id else None

    return NormalizedTicket(
        ticket_id=ticket_id,
        message=message,
        email=email,
        order_id=order_id,
        product_id=product_id,
        raw=raw,
    )
