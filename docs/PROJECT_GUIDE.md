# ShopWave — Project Guide (Full Overview)

**Autonomous Support Resolution Agent** for the KSOLVES-style hackathon brief: fictitious retailer **ShopWave**, **20 mock tickets**, ingest → triage → tool-based resolution or escalation → full audit trail.

This document summarizes the whole repository: purpose, architecture, features, configuration, and how to run and demo it.

---

## 1. What this project does

| Stage | Implementation |
|--------|----------------|
| **Ingest** | Loads JSON + markdown from `agentic_ai_hackthon_2026_sample_data-main/` (`tickets.json`, customers, orders, products, knowledge base). |
| **Classify & triage** | **Category**, **urgency**, **resolvability** — rule-based keywords plus optional **LLM triage** (Ollama / OpenAI) merged conservatively with rules. |
| **Resolve** | Uses **mock CRM tools**: lookups, refund eligibility, issue refund, KB search, send reply. |
| **Escalate** | `escalate(ticket_id, summary, priority)` with structured summary (attempted tools, failures, reason, triage). |
| **Audit** | Every step: **thought**, **tool name**, **attempt**, **result**, **status** → `logs/audit_log.json` + optional live WebSocket stream in the UI. |

**Concurrency:** All **20 tickets** run in parallel via `asyncio.gather`, not one-by-one.

---

## 2. Hackathon criteria (how this repo maps)

| Requirement | Where it shows up |
|-------------|-------------------|
| **≥3 tool calls in a chain** | Typical flow: `begin_ticket_session` → lookups → policy/KB → `send_reply` or `escalate`; escalation path pads with KB calls if needed (`_ensure_chain_before_escalate`). |
| **Handle tool failures** | Retries with backoff (`agent/retry.py`); simulated timeout on `get_order` / malformed KB when `AGENT_SIMULATE_FAULTS=1`; validation rejects bad tool payloads. |
| **Parallel tickets** | `agent/runner.py` — `asyncio.gather` over `process_ticket`. |
| **Explainability** | Each step has a **thought** string; audit JSON + UI expandable traces; optional **`llm_triage`** logs full merge metadata. |

---

## 3. Repository layout

```
hackathonex/
├── agent/                    # Core agent logic (Python package)
│   ├── main.py               # CLI entry: python -m agent.main
│   ├── runner.py             # Batch runner: load dataset, gather, persist logs
│   ├── processor.py          # SupportAgentProcessor — triage, branches, tools
│   ├── tools.py              # Mock ToolContext (CRM / policy)
│   ├── llm_triage.py         # Optional Ollama / OpenAI JSON triage + merge
│   ├── env_bootstrap.py      # Loads .env from project root
│   ├── data_loader.py        # Dataset paths + normalize_ticket
│   ├── models.py             # NormalizedTicket, AuditStep, TicketResult
│   └── retry.py              # with_retry (backoff + jitter)
├── api/
│   └── server.py             # FastAPI + WebSocket + static SPA
├── web/                      # React + Vite + Tailwind dashboard
│   └── dist/                 # Built UI (after npm run build)
├── docs/
│   ├── PROJECT_GUIDE.md      # This file
│   ├── PROJECT_GUIDE.html    # Print-friendly → Save as PDF from browser
│   ├── architecture.md       # Mermaid diagram
│   └── failure_modes.md      # Pointer → ../failure_modes.md
├── logs/
│   ├── audit_log.json        # Generated after each batch run (gitignored)
│   └── dead_letter.json      # Escalation transport failures (if any)
├── architecture.png          # 1-page architecture diagram (submission)
├── failure_modes.md          # Failure scenarios (submission; canonical)
├── audit_log.json            # Committed demo output — all 20 tickets (submission)
├── agentic_ai_hackthon_2026_sample_data-main/   # Hackathon sample data
├── .env.example              # Template environment variables
├── requirements.txt          # Python dependencies
├── package.json              # npm workspace root (includes web/)
├── Dockerfile                # Multi-stage: build UI + run uvicorn
└── README.md                 # Quick start
```

---

## 4. Tools (mock API surface)

**Read / lookup**

- `get_customer(email)`
- `get_order(order_id)`
- `get_product(product_id)`
- `search_knowledge_base(query)`

**Write / act**

- `check_refund_eligibility(order_id, message=…)` — policy logic in code
- `issue_refund(order_id, amount)` — amount must match order (irreversible guard)
- `send_reply(ticket_id, message)`
- `escalate(ticket_id, summary, priority)` — `priority` ∈ {high, medium, low}

**Session / audit helpers**

- `begin_ticket_session` — records triage metadata at session start

**Optional**

- **`llm_triage`** — not a CRM tool; an audited step when LLM classification is enabled

Fault injection (when `AGENT_SIMULATE_FAULTS=1`): first `get_order` can timeout once; first KB response can be malformed once (retries exercise recovery).

---

## 5. Optional LLM triage (Llama via Ollama or OpenAI)

- **Purpose only:** classify **category / urgency / resolvability**; output must match fixed enums; merged with **rule-based** triage (conservative).
- **Does not replace** refund math, eligibility rules, or database tools.
- **Enable:** `SHOPWAVE_USE_LLM_TRIAGE=1` and either `OLLAMA_BASE_URL` (e.g. `http://127.0.0.1:11434`) + `OLLAMA_MODEL`, or `OPENAI_API_KEY` (+ optional `OPENAI_MODEL`, `OPENAI_BASE_URL`).
- **Config:** Copy `.env.example` to `.env` in the project root; variables load via `env_bootstrap` for CLI and API.

---

## 6. Command Center (web UI)

- **Stack:** FastAPI serves built static files from `web/dist`; **WebSocket** `/ws/live` streams JSON events (`run_begin`, `ticket_begin`, `tool_step`, `ticket_complete`, `run_complete`).
- **Trigger:** `POST /api/run` with body `{"simulate_faults": true|false}`.
- **Dev:** `npm run dev` in `web/` with Vite proxy to API (see README).

---

## 7. Environment variables (reference)

| Variable | Role |
|----------|------|
| `AGENT_SIMULATE_FAULTS` | `1` / `true` — flaky tool simulation |
| `SHOPWAVE_USE_LLM_TRIAGE` | Enable LLM triage step |
| `OLLAMA_BASE_URL` | Ollama server URL (if set, preferred over OpenAI) |
| `OLLAMA_MODEL` | Model name (default `llama3.2`) |
| `OPENAI_API_KEY` | OpenAI-compatible API key |
| `OPENAI_MODEL` | Default `gpt-4o-mini` |
| `OPENAI_BASE_URL` | Optional API base URL |

---

## 8. How to run (short)

**CLI batch**

```bash
pip install -r requirements.txt
python -m agent.main
```

**Dashboard + API**

```bash
pip install -r requirements.txt
npm install
npm run build
python -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, connect WebSocket, click **Run all tickets**.

**Docker**

```bash
docker build -t shopwave-agent .
docker run --rm -p 8000:8000 shopwave-agent
```

---

## 9. Submission / demo checklist (typical hackathon)

| Deliverable | This repo |
|-------------|-----------|
| Runnable code + entry point | `python -m agent.main`, `uvicorn api.server:app` |
| README | `README.md` |
| Architecture | `docs/architecture.md` + section 3 above |
| Failure modes | `docs/failure_modes.md` |
| Demo (20 tickets) | CLI counts 20; UI runs full batch with live trace |

---

## 10. Exporting this document as PDF

1. Open **`docs/PROJECT_GUIDE.html`** in Chrome / Edge.
2. **Print** → **Save as PDF** (enable “Background graphics” if you want styled headers).

Alternatively convert **`docs/PROJECT_GUIDE.md`** with Pandoc, VS Code Markdown PDF, or any Markdown exporter.

---

## 11. Contact / branding (submission)

Project title: **ShopWave Autonomous Support Agent**. Sample data folder name reflects the official hackathon sample dataset naming.

---

*Generated as part of the hackathonex repository. Update this file when architecture or env vars change.*
