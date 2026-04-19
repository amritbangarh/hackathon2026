# Architecture — ShopWave Support Agent

One-page technical overview for submission: **agent loop**, **tools**, **state**.

## Diagram

```mermaid
flowchart TB
    subgraph ingest["Ingest"]
        DL[data_loader.load_dataset]
        NT[normalize_ticket ×20]
    end

    subgraph runtime["Concurrent execution"]
        G[asyncio.gather process_ticket]
    end

    subgraph loop["Agent loop — SupportAgentProcessor"]
        TR[triage_ticket\n category / urgency / resolvability]
        BT[begin_ticket_session]
        TC[_call_tool + with_retry\n schema validation]
        POL[policy branches\n refund / KB / escalate]
    end

    subgraph tools["Tool mocks — ToolContext"]
        R["READ: get_customer, get_order,\n get_product, search_knowledge_base"]
        W["WRITE: check_refund_eligibility(order_id),\n issue_refund(order_id, amount),\n send_reply, escalate(..., priority)"]
        FAULT["Optional faults:\n TimeoutError, malformed KB"]
    end

    subgraph memory["Memory / state"]
        DS[(In-memory dataset dict\n customers / orders / products / KB)]
        OD["Order mutation under lock\n refund_status updates"]
        AUD[(logs/audit_log.json)]
        DLQ[(logs/dead_letter.json)]
    end

    subgraph ui["Command Center — web dist"]
        API[FastAPI + Uvicorn]
        WS["/ws/live JSON stream"]
        SPA[Vite React dashboard]
    end

    DL --> NT --> G
    G --> loop
    TR --> BT --> TC --> POL
    POL --> tools
    tools --> DS
    POL --> OD
    POL --> AUD
    POL --> DLQ

    ES[event_sink → broadcast]
    POL -. streaming events .-> ES
    ES --> WS
    API --> SPA
    WS --> SPA
```

## Command Center UI

The **FastAPI** app (`api/server.py`) serves the production **React** bundle under `web/dist`. During `POST /api/run`, `run_batch` emits structured events (`ticket_begin`, `tool_step`, `ticket_complete`, …) through an async callback to **WebSocket** subscribers — no polling, live concurrency-visible traces.

## Agent loop

For each ticket, the processor runs a **mostly deterministic** pipeline: **rule-based triage** (always), optional **LLM-assisted triage** (`llm_triage` audit step when `SHOPWAVE_USE_LLM_TRIAGE` + OpenAI or Ollama is configured) → session → lookups → eligibility / refund / reply or escalate. Refunds and writes do **not** use an LLM. Every tool invocation goes through `_call_tool`, which records an **AuditStep** (`thought`, tool name, attempt count, serialized result, status).

## Tool design

Mocks mirror the hackathon PDF: lookups return JSON-shaped dicts or `None`; writes mutate order refund state idempotently where applicable. **Schema validation** (`_validate_tool_output`) gates success before the agent trusts a response.

## Memory / state

- **Ephemeral**: loaded JSON dataset in `ToolContext`; no cross-run database.
- **Durability**: **audit** JSON and **dead-letter** JSON after each batch run.
