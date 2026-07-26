# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ARIA (Automated Restaurant Inventory Assistant) — a conversational AI for hotel/restaurant inventory management. Workers type commands; ARIA understands intent, confirms before writing, and persists changes to a PostgreSQL/SQLite database. It uses Claude (Anthropic) for all LLM calls, a local embedding model (fastembed) for semantic memory/retrieval, and Mem0 (optional) for cross-session worker memory.

Speech (voice input/TTS) has been removed — Claude has no speech API, so this is now a text-first assistant. The `/api/voice/process` and `/api/voice/stream` endpoints remain (they're text endpoints despite the router name); `/transcribe` and `/synthesize` are gone.

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

## Evals (no server required)

Two suites; run BOTH as the gate for any prompt/model/pipeline change:

```bash
cd backend
source venv/bin/activate

# 1. Prompt eval — 29 cases against aria_process() in isolation, synthetic context
python -m eval.run_eval --output eval/results/latest.json
#    Filters: --tags happy_path fragment | --ids simple_add
#    Keep --concurrency at the default 2 — 5 parallel Claude API calls hit the
#    tokens-per-minute limit, trip the circuit breaker, and cascade ERRORs.

# 2. Graph eval — multi-turn scenarios through ChatService.handle() against a
#    throwaway SQLite DB, with per-turn DB assertions (rows written, pending state)
python -m eval.run_graph_eval --output eval/results/graph_latest.json
#    Filters: --ids add_confirm_affirm | --tags fragment | --skip-tags gate_b3
```

Shared grading primitives live in `backend/eval/common.py`. Baselines:
`eval/results/baseline_post_pgvector.json` (prompt, 28/29 — the one failure is
the RULE 1 flake now fixed deterministically) and `eval/results/graph_baseline.json` (10/10).

---

## Environment variables (`backend/.env`)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required. All LLM calls use Claude (Anthropic). |
| `WORKER_ID` | Worker identifier injected into every chat turn. |
| `MODEL` | Main ARIA model (default `claude-haiku-4-5`). |
| `INTENT_MODEL` | Intent classifier model (default `claude-haiku-4-5`). |
| `DATABASE_URL` | PostgreSQL (`postgresql://...`) or omit for SQLite. |
| `MEM0_API_KEY` | Optional. Enables long-term worker memory via Mem0 cloud. If absent, falls back to local pgvector (`vector_memory_service.py`). |
| `INTENT_CONFIDENCE_THRESHOLD` | Confidence floor for intent routing (default `0.65`). Below this → `clarify_node`. |
| `SCREEN_MODEL` | Model for the merged screen+extract call (default `claude-haiku-4-5`). |
| `ARIA_STREAMING` | Set `1` to stream ARIA's message token-by-token as SSE `chunk` events (default off). Implemented via a forced tool-use stream, reading `message` field text out of `input_json_delta` fragments (Claude has no structured-parse streaming helper). |

No embeddings or speech env vars are needed: embeddings run locally via `fastembed` (no API key, weights cached on first use), and speech (STT/TTS) has been removed — Claude has no equivalent API.

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
  ├── load_context_node   (DB: inventory state + summary dict, history, pending actions)
  └── load_memory_node    (Mem0/pgvector: worker personality, lexicons, tone)
        ↓ (fan-in)
    preprocess_node       (sync: fuzzy unit detection, fragment hints, affirmation check)
        ↓
    intent_node           (claude-haiku-4-5 — returns IntentResult with confidence)
        ↓
   ┌────┴─────┐
clarify_node  screen_extract_node  (confidence < 0.65 → clarify; else ONE claude-haiku-4-5
    ↓              ↓                call that merges food screening + strict item extraction,
   END      ┌──────┴──────┐         and deterministically corrects impossible units)
        rejected_node  priority_node  (no LLM — BM25/pgvector prunes context)
            ↓                ↓
           END           aria_node    (claude-haiku-4-5 — conversational response + ARIAResult.
                             ↓         Short-circuits WITHOUT an LLM call for:
                             ↓         affirmations, and total-value analytics questions
                             ↓         answered from Python-computed figures)
                        validate_node   (deterministic Python checks: non-food, unit mismatch,
                                         unit auto-correction backstop, suspicious qty,
                                         storage mismatch, same-day conflict, qty-bleed guard)
                             ↓
                        execute_node    (DB writes + conversation persistence)
                             ↓
                      persist_memory_node  (write behavioural insights to memory)
                             ↓
                            END
```

Happy-path LLM calls per turn: **3** (intent claude-haiku-4-5 → screen_extract claude-haiku-4-5 → ARIA claude-haiku-4-5).
Analytics totals and affirmations skip ARIA entirely.

### Key files

| Path | Role |
|---|---|
| `backend/workflow/graph.py` | Builds and returns the compiled LangGraph graph. |
| `backend/workflow/nodes.py` | All node implementations — the core of every turn. |
| `backend/workflow/state.py` | `WorkflowState` TypedDict — all fields nodes can read/write. |
| `backend/agents/aria_agent.py` | ARIA's system prompt (10 explicit rules) + `aria_process()`. |
| `backend/agents/intent_agent.py` | Intent classifier (cheap, dedicated, fast path). |
| `backend/agents/screen_extract_agent.py` | ONE claude-haiku-4-5 call merging food screening (former guard) + strict item extraction. Uses a forced tool call (`ScreenedExtractionResult`'s schema hits Claude's strict-decoding complexity ceiling — see `clients/llm_client.py`), validated via Pydantic. |
| `backend/agents/priority_agent.py` | Context pruner between screen_extract and ARIA — uses BM25 (SQLite) or pgvector ANN (Postgres) to rank and trim conversation history and inventory context. No LLM call. |
| `backend/agents/schemas.py` | Pydantic schemas for all LLM outputs (`ARIAResult`, `IntentResult`, `ScreenedExtractionResult`). |
| `backend/services/analytics_fastpath.py` | Deterministic total-value answers from Python-computed figures — LLM never does arithmetic. Short-circuit at the top of `aria_node`. |
| `backend/utils/json_stream.py` | `MessageFieldExtractor` — incremental extraction of the streamed `message` field for token streaming (`ARIA_STREAMING=1`). |
| `backend/services/chat_service.py` | SSE streaming + sync entry points; emits a `TURN SUMMARY` log block per turn. |
| `backend/services/ai_service.py` | DB helpers: inventory context builder, pending actions store, validation utils. |
| `backend/services/vector_memory_service.py` | Local pgvector replacement for Mem0 — stores per-worker behavioural insights with HNSW index; activated when `MEM0_API_KEY` is absent. Embeddings generated locally via `fastembed` (`BAAI/bge-small-en-v1.5`, 384 dims) — no API key, no network call. |
| `backend/clients/llm_client.py` | Shared `AsyncAnthropic` singleton; reads `MODEL` / `INTENT_MODEL` / `SCREEN_MODEL` env vars. |
| `backend/clients/mem0_client.py` | Mem0 async wrappers; routes to `vector_memory_service` when `MEM0_API_KEY` is absent. |
| `backend/utils/circuit_breaker.py` | Three-state circuit breaker (closed/open/half-open) wrapping all Claude API calls. Opens after 3 consecutive failures; probes after 60 s. |
| `backend/utils/llm_retry.py` | `call_llm()` helper used by all agents — exponential backoff + circuit breaker integration. |
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

SSE events are named: `start`, `status`, `chunk`, `done`, `error`. The `done` event carries the final response payload. `chunk` events (only with `ARIA_STREAMING=1`) stream ARIA's message text incrementally; the frontend renders them with a typing cursor and replaces the streamed text atomically with `done.message` — note validate/execute may rewrite the message after generation, so the streamed text can differ from the final one on warning turns.

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
