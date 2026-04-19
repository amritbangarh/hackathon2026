# ShopWave — Autonomous Support Resolution Agent

Hackathon submission: ingests mock support tickets, **triage** (category, urgency, resolvability), resolves via **tool calls** with **audit logging**, retries, and optional **fault injection**. Processes all **20** sample tickets **concurrently** (`asyncio.gather`).

## Prerequisites

- Python **3.10+**
- Node **20+** (only needed to rebuild the dashboard UI from `web/`)

## Tech stack

| Layer | Technologies |
|-------|----------------|
| **Language / runtime** | Python **3.12** (Dockerfile), **3.10+** locally |
| **Agent & APIs** | **FastAPI**, **Uvicorn**, **Pydantic**, **httpx**, **python-dotenv** |
| **Concurrency** | **asyncio** (`asyncio.gather` over tickets) |
| **Dashboard** | **React 18**, **TypeScript**, **Vite**, **Tailwind CSS** |
| **Deployment** | **Docker** (multi-stage: Node build UI → Python runtime) |
| **Optional triage** | **OpenAI**-compatible API or **Ollama** (classification only; tools stay deterministic) |

## Hackathon submission files (Step 2)

| Deliverable | Location |
|-------------|----------|
| **README.md** | This file — setup, run paths, tech stack |
| **architecture.png** | Repo root — one-page agent loop & tool diagram |
| **failure_modes.md** | Repo root — ≥3 failure scenarios + handling |
| **audit_log.json** | Repo root — demo run output for **all 20 tickets** (regenerate after runs with `python -m agent.main` or dashboard; copy from `logs/audit_log.json` if needed) |

## Entry points

### 1 · Command-line batch (CLI)

From the repository root:

```bash
pip install -r requirements.txt   # FastAPI UI optional for CLI-only
python -m agent.main
```

### 2 · Command Center dashboard (recommended for demos)

Production-style: API serves the built SPA and streams **live WebSocket telemetry** during a run.

**One terminal — API + static UI** (bash / macOS / Linux):

```bash
pip install -r requirements.txt
npm install          # installs the web workspace (see root package.json)
npm run build        # builds dashboard → web/dist
uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

*(Alternative without workspaces: `cd web && npm install && npm run build`.)*

**Same steps on Windows PowerShell** (use `;` instead of `&&` where `&&` is not supported):

```powershell
pip install -r requirements.txt
npm install
npm run build
python -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

If you prefer explicit paths, put **`npm` options before `install`** (otherwise npm may read `package.json` from the wrong folder):

```powershell
npm --prefix web install
npm --prefix web run build
```

Open **http://127.0.0.1:8000** → click **Run all tickets**. Expand any ticket card for the full explainable tool chain.

**Develop UI with hot reload (two terminals):**

```bash
# Terminal A
uvicorn api.server:app --reload --host 127.0.0.1 --port 8000

# Terminal B (bash)
cd web && npm install && npm run dev
```

PowerShell terminal B:

```powershell
Set-Location web; npm install; npm run dev
```

Open **http://127.0.0.1:5173** — Vite proxies `/api` and `/ws` to port 8000.

### Troubleshooting (Windows)

**`npm ERR! ENOENT … package.json` at the repo root**

Do **not** rely on `npm install --prefix web` alone on some npm versions — use **`npm --prefix web install`** (note the word order), **`cd web` then `npm install`**, or run **`npm install`** from the repo root (this project includes a root `package.json` **workspace** so one install covers `web/`).

Also avoid pasting a **multi-line block** into PowerShell when it turns lines into `>>` continuations; run **one command per line**, or join with **`;`**.

**`WinError 32` when pip upgrades `uvicorn`**

Something still has `uvicorn.exe` open (usually another terminal running the API). Stop it: close that terminal, or end the process (Task Manager → end `uvicorn` / Python), then run `pip install -r requirements.txt` again.

**`WARNING: Ignoring invalid distribution ~vicorn`**

A broken partial install folder exists under `Python311\Lib\site-packages` (often named `~vicorn`). After closing apps using uvicorn, delete that folder (and any `~vicorn.dist-info` if present), then reinstall.

**Long `pip` “dependency conflicts” warnings (chromadb, crewai, langchain, …)**

Those come from **other packages in your global/user Python**. They do not affect this repo if FastAPI starts. For a clean isolate, use a **venv**:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

**Fallback — run the API without overwriting the global `uvicorn` script:**

```powershell
python -m pip install -r requirements.txt --user
python -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

### Environment

| Variable | Purpose |
|----------|---------|
| `AGENT_SIMULATE_FAULTS` | `1` / `true` / `yes`: **timeouts** on first simulated `get_order`, **malformed KB** payload once — exercises retries + schema validation. |
| **`SHOPWAVE_USE_LLM_TRIAGE`** | **`1`** / **`true`** — enable **optional LLM triage** (classification only). Refunds and CRM actions stay **deterministic tools**. Requires **`OPENAI_API_KEY`** or **`OLLAMA_BASE_URL`**. |
| **`OPENAI_API_KEY`** | OpenAI API key (`OPENAI_MODEL` defaults to `gpt-4o-mini`, optional `OPENAI_BASE_URL` for proxies). |
| **`OLLAMA_BASE_URL`** | e.g. **`http://127.0.0.1:11434`** — uses **`OLLAMA_MODEL`** (default `llama3.2`). If set, Ollama is preferred over OpenAI. |

When LLM triage is **off** or keys are missing, the agent uses **rule-based triage only**; the audit step `llm_triage` is recorded as **`skipped`** with the reason in JSON.

**`.env` file:** Copy **`.env.example`** to **`.env`** in the project root (`cp .env.example .env` or copy in Explorer). Variables are loaded automatically for `python -m agent.main`, `uvicorn api.server:app`, and dashboard runs — no need to export them manually each time.

### Outputs

- `logs/audit_log.json` — triage, outcome, every tool step (thought, tool, attempt, status), final_decision, confidence  
- **`audit_log.json` (repo root)** — copy committed for submission; mirror of the latest full batch run  
- `logs/dead_letter.json` — escalation transport failures  

### Docker (full stack: UI build + API)

```bash
docker build -t shopwave-agent .
docker run --rm -p 8000:8000 shopwave-agent
```

Then open **http://localhost:8000**.

With fault simulation:

```bash
docker run --rm -p 8000:8000 -e AGENT_SIMULATE_FAULTS=1 shopwave-agent
```

### API quick reference

| Endpoint | Purpose |
|----------|---------|
| `GET /api/meta` | Dataset info (20 tickets) |
| `GET /api/tickets` | Ticket summaries for the UI matrix |
| `POST /api/run` | Body `{"simulate_faults": bool}` — starts parallel batch (async task) |
| `WS /ws/live` | JSON event stream (`run_begin`, `ticket_begin`, `tool_step`, `ticket_complete`, `run_complete`) |
| `GET /docs` | OpenAPI |

## Documentation

- **[Full project guide (Markdown)](docs/PROJECT_GUIDE.md)** — overview, repo layout, tools, env vars, checklist  
- **[Project guide (HTML → Print / Save as PDF)](docs/PROJECT_GUIDE.html)** — open in browser, Print → Save as PDF  
- **[architecture.png](architecture.png)** — 1-page diagram (submission)  
- [Architecture (Mermaid source)](docs/architecture.md) — same design in Markdown  
- **[failure_modes.md](failure_modes.md)** — failure scenarios (submission; copy also under `docs/`)  

## Demo checklist

Show **all 20 tickets** end-to-end: either CLI output + `logs/audit_log.json`, or the **dashboard** run with expanded audit trails on several tickets.
