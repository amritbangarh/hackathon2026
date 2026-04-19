from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .data_loader import load_dataset, normalize_ticket
from .env_bootstrap import load_app_env
from .models import TicketResult
from .processor import SupportAgentProcessor
from .tools import ToolContext


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


EventSink = Callable[[dict[str, Any]], Awaitable[None]] | None


async def run_batch(
    root: Path,
    *,
    simulate_faults: bool | None = None,
    event_sink: EventSink = None,
) -> list[TicketResult]:
    """Load all tickets, process in parallel (asyncio.gather), persist audit logs."""
    load_app_env()
    if simulate_faults is None:
        simulate_faults = _env_truthy("AGENT_SIMULATE_FAULTS")

    dataset = load_dataset(root)
    normalized = [normalize_ticket(t) for t in dataset["tickets"]]

    if event_sink:
        await event_sink({"type": "run_begin", "ticket_count": len(normalized)})

    tool_ctx = ToolContext(dataset, simulate_faults=simulate_faults)
    processor = SupportAgentProcessor(
        tool_ctx=tool_ctx,
        logs_dir=root / "logs",
        event_sink=event_sink,
    )

    results = await asyncio.gather(*(processor.process_ticket(ticket) for ticket in normalized))
    await processor.persist_logs(results)

    if event_sink:
        processed = len(results)
        resolved = sum(1 for r in results if r.status == "resolved")
        escalated = sum(1 for r in results if r.escalated)
        failed = sum(1 for r in results if r.failed and not r.dead_lettered)
        dead = sum(1 for r in results if r.dead_lettered)
        await event_sink(
            {
                "type": "run_complete",
                "processed_count": processed,
                "resolved_count": resolved,
                "escalated_count": escalated,
                "failed_count": failed,
                "dead_letter_count": dead,
            }
        )

    return results
