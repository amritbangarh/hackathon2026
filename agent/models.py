from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedTicket:
    ticket_id: str
    message: str
    email: str | None = None
    order_id: str | None = None
    product_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditStep:
    thought: str
    tool_called: str
    attempt: int
    result: str
    status: str


@dataclass
class TicketResult:
    ticket_id: str
    final_decision: str
    confidence: float
    steps: list[AuditStep]
    status: str
    triage: dict[str, str] = field(default_factory=dict)
    outcome: str = ""
    escalation_priority: str | None = None
    escalated: bool = False
    failed: bool = False
    dead_lettered: bool = False
