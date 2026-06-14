# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ARIA (Automated Restaurant Inventory Assistant) — a voice-first conversational AI for hotel/restaurant inventory management. Workers speak or type commands; ARIA understands intent, confirms before writing, and persists changes to a PostgreSQL/SQLite database. It uses OpenAI for LLM calls and Mem0 for cross-session worker memory.

---

## Running the project

**Backend** (Python 3.10+, FastAPI):
```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```
API docs at `http://localhost:8000/docs`. A vanilla static chat UI is mounted at `http://localhost:8000/chat`.

**Frontend** (React + Vite + Tailwind):
```bash
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:5173`.

---

## Environment variables (`backend/.env`)

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Required. All LLM calls use OpenAI. |
| `MODEL` | Main model (default `gpt-4o`). |
| `INTENT_MODEL` | Intent classifier model (default `gpt-4o-mini`). |
| `DATABASE_URL` | PostgreSQL (`postgresql://...`) or omit for SQLite. |
| `MEM0_API_KEY` | Optional. Enables long-term worker memory via Mem0. If absent, system falls back to DB-based profile. |

---

## Architecture

### Request flow (backend)

Every chat turn goes through a **LangGraph** graph compiled in `backend/workflow/graph.py`. The graph is a **module-level singleton** (no checkpointer — stateless per request). `ChatService` in `backend/services/chat_service.py` is the single entry point — it either runs the graph synchronously (`handle()`) or streams it as SSE frames (`handle_stream()`).

```
POST /api/chat  →  ChatService.handle()
POST /api/chat/stream  →  ChatService.handle_stream()  (SSE)
```

**Graph topology** (`backend/workflow/graph.py`):
```
START
  ├── load_context_node   (DB: inventory state, conversation history, pending actions)
  └── load_memory_node    (Mem0: worker personality, lexicons, tone)
        ↓ (fan-in)
    preprocess_node       (sync: fuzzy unit detection, fragment hints, affirmation check)
        ↓
    intent_node           (gpt-4o-mini, temp=0 — returns IntentResult with confidence)
        ↓
   ┌────┴─────┐
clarify_node  guard_node  (confidence < 0.65 → clarify; else validate items are food)
    ↓              ↓
   END      ┌──────┴──────┐
        rejected_node  extraction_node  (strict slot parsing via ExtractionResult schema)
            ↓                ↓
           END           aria_node      (gpt-4o — full conversational response + ARIAResult)
                             ↓
                        validate_node   (deterministic Python checks: non-food, unit mismatch,
                                         suspicious qty, storage mismatch, same-day conflict)
                             ↓
                        execute_node    (DB writes + conversation persistence)
                             ↓
                      persist_memory_node  (write behavioural insights to Mem0)
                             ↓
                            END
```

### Key files

| Path | Role |
|---|---|
| `backend/workflow/graph.py` | Builds and returns the compiled LangGraph graph. |
| `backend/workflow/nodes.py` | All node implementations — the core of every turn. |
| `backend/workflow/state.py` | `WorkflowState` TypedDict — all fields nodes can read/write. |
| `backend/agents/aria_agent.py` | ARIA's system prompt (10 explicit rules) + `aria_process()`. |
| `backend/agents/intent_agent.py` | Intent classifier (cheap, dedicated, fast path). |
| `backend/agents/extraction_agent.py` | Strict structured item extraction. |
| `backend/agents/guard_agent.py` | Rejects non-food items before ARIA runs. |
| `backend/agents/schemas.py` | Pydantic schemas for all LLM outputs (`ARIAResult`, `IntentResult`, `ExtractionResult`). |
| `backend/services/chat_service.py` | SSE streaming + sync entry points; emits a `TURN SUMMARY` log block per turn. |
| `backend/services/ai_service.py` | DB helpers: inventory context builder, pending actions store, validation utils. |
| `backend/clients/llm_client.py` | Shared `AsyncOpenAI` singleton; reads `MODEL` / `INTENT_MODEL` env vars. |
| `backend/clients/mem0_client.py` | Mem0 async wrappers; gracefully degrades if `MEM0_API_KEY` is absent. |
| `backend/routers/chat.py` | `/api/chat`, `/api/chat/stream`, `/api/memory` endpoints. |
| `backend/main.py` | FastAPI app, WebSocket broadcast manager, auto-migration on startup. |

### Pending-action state

"Confirm before update" is implemented via an in-memory dict `_pending_actions` (in `ai_service.py`), keyed by `session_id`. When ARIA returns `action=confirm`, the items are stored here. The next affirmation from the worker triggers `is_affirmation=True` in `preprocess_node`, which short-circuits ARIA and executes directly. This store is in-process and lost on restart — multi-process deployments would need an external store.

### LLM output safety

All LLM outputs are parsed through Pydantic models in `backend/agents/schemas.py` using `Literal[...]` for every enum field. `ARIAResult` model validators enforce:
- `action=update` requires `confirmed=True` (downgrades to `confirm` otherwise)
- `action=confirm/update` requires at least one item with a parsed quantity (downgrades to `clarify`)

### Frontend

React SPA at `frontend/src/`. Communicates with the backend via:
- `POST /api/chat/stream` (SSE) for real-time turn streaming
- REST for inventory CRUD and analytics
- WebSocket `/ws` for multi-worker broadcast

SSE events are named: `start`, `status`, `done`, `error`. The `done` event carries the final response payload.

---

## Database

SQLAlchemy models in `backend/models.py`. Default is SQLite (auto-created). Switch to PostgreSQL by setting `DATABASE_URL`. Schema migrations run automatically at startup in `main.py:_run_migrations()` — it uses `ALTER TABLE ADD COLUMN IF NOT EXISTS` style checks, so it is safe to re-run.

---

## Logging

Structured JSON-ish logging via `backend/logging_config.py`. Every turn has a trace ID visible in `TURN SUMMARY` blocks. To follow a single turn end-to-end:
```bash
grep "<trace_id>" aria_output.log
```
Nodes log with `NODE ──▶ <name>` at entry and `NODE ◀── <name>` at exit. Edge case branches are tagged `[EDGE CASE]` and anomalies with `[ANOMALY]`.
