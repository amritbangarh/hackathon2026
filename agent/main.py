from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .env_bootstrap import load_app_env
from .runner import run_batch

load_app_env()


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def run() -> None:
    root = Path(__file__).resolve().parent.parent
    results = await run_batch(root, simulate_faults=_env_truthy("AGENT_SIMULATE_FAULTS"))

    processed_count = len(results)
    resolved_count = sum(1 for r in results if r.status == "resolved")
    escalated_count = sum(1 for r in results if r.escalated)
    failed_count = sum(1 for r in results if r.failed and not r.dead_lettered)
    dead_letter_count = sum(1 for r in results if r.dead_lettered)

    print(f"processed_count={processed_count}")
    print(f"resolved_count={resolved_count}")
    print(f"escalated_count={escalated_count}")
    print(f"failed_count={failed_count}")
    print(f"dead_letter_count={dead_letter_count}")


if __name__ == "__main__":
    asyncio.run(run())
