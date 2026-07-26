import json
import logging
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy import text, inspect

from logging_config import setup_logging, J, new_trace, separator
from database import engine, Base, SessionLocal, create_views
from routers import inventory, voice, analytics, conversations, locations, users
from routers import chat as chat_router

# ── Boot logging immediately so every import after this point is captured ─────
setup_logging()
logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WS CONNECT  active=%d  client=%s", len(self.active), ws.client)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        logger.info("WS DISCONNECT  active=%d", len(self.active))

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)
        logger.debug("WS BROADCAST  payload=%s  dead=%d", J(message), len(dead))


manager = ConnectionManager()


def _ensure_vector_dim(conn, table: str, column: str, dims: int):
    """Drop and recreate a pgvector column if its dimension doesn't match `dims`.

    Embeddings from a different model aren't reinterpretable as another
    model's vector space — when the embedding provider/model changes (as it
    did when this table moved from OpenAI text-embedding-3-small to local
    fastembed), existing vectors must be cleared and regenerated at the new
    dimension rather than left mismatched (which pgvector would reject on
    every insert/query).
    """
    # table is always an internal literal (never user input) — safe to inline.
    # SQLAlchemy's named-bind-param substitution breaks when a param is
    # immediately followed by a `::cast`, so the table name can't be bound.
    row = conn.execute(text(
        f"SELECT format_type(atttypid, atttypmod) AS coltype "
        f"FROM pg_attribute "
        f"WHERE attrelid = '{table}'::regclass AND attname = :column AND NOT attisdropped"
    ), {"column": column}).fetchone()
    if row is None:
        return  # column doesn't exist yet — caller adds it fresh
    if row.coltype != f"vector({dims})":
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} vector({dims})"))
        logger.warning(
            "MIGRATION  reset %s.%s  %s → vector(%d)  (embeddings cleared — embedding model changed)",
            table, column, row.coltype, dims,
        )


def _run_migrations():
    """Automatically add any missing columns to existing tables."""
    from database import DATABASE_URL
    from services.vector_memory_service import EMBEDDING_DIMS
    is_postgres = DATABASE_URL.startswith("postgresql")

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    logger.info("MIGRATION CHECK  existing_tables=%s", existing_tables)

    if "inventory" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("inventory")}
        migrations = [
            ("storage_area",  "VARCHAR(100) DEFAULT 'General Storage'"),
            ("location_name", "VARCHAR(100) DEFAULT ''"),
            ("unit_price",    "FLOAT"),
        ]
        if is_postgres:
            migrations.append(("name_embedding", f"vector({EMBEDDING_DIMS})"))
        with engine.connect() as conn:
            for col, definition in migrations:
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE inventory ADD COLUMN {col} {definition}"))
                    logger.info("MIGRATION  added column inventory.%s (%s)", col, definition)
            if is_postgres and "name_embedding" in existing_cols:
                _ensure_vector_dim(conn, "inventory", "name_embedding", EMBEDDING_DIMS)
            conn.commit()

    if "user_profiles" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("user_profiles")}
        profile_migrations = [
            ("tone_preference", "VARCHAR(50) DEFAULT 'friendly_fun'"),
        ]
        with engine.connect() as conn:
            for col, definition in profile_migrations:
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE user_profiles ADD COLUMN {col} {definition}"))
                    logger.info("MIGRATION  added column user_profiles.%s (%s)", col, definition)
            conn.commit()

    if "conversations" in existing_tables and is_postgres:
        existing_cols = {c["name"] for c in inspector.get_columns("conversations")}
        with engine.connect() as conn:
            if "turn_embedding" not in existing_cols:
                conn.execute(text(f"ALTER TABLE conversations ADD COLUMN turn_embedding vector({EMBEDDING_DIMS})"))
                logger.info("MIGRATION  added column conversations.turn_embedding (vector(%d))", EMBEDDING_DIMS)
            else:
                _ensure_vector_dim(conn, "conversations", "turn_embedding", EMBEDDING_DIMS)
            conn.commit()

    # Create new tables if they don't exist yet (idempotent — SQLAlchemy skips existing tables)
    from models import PendingAction, RejectedItem, SessionSummary, WorkerMemory
    for model in (PendingAction, RejectedItem, SessionSummary, WorkerMemory):
        if model.__tablename__ not in existing_tables:
            model.__table__.create(bind=engine, checkfirst=True)
            logger.info("MIGRATION  created table %s", model.__tablename__)

    if is_postgres:
        with engine.connect() as conn:
            _ensure_vector_dim(conn, "worker_memories", "embedding", EMBEDDING_DIMS)
            conn.commit()

    # pgvector HNSW indexes — idempotent, only on PostgreSQL
    if is_postgres:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS worker_memories_embedding_idx "
                "ON worker_memories USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS inventory_name_embedding_idx "
                "ON inventory USING hnsw (name_embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS conversations_turn_embedding_idx "
                "ON conversations USING hnsw (turn_embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            ))
            conn.commit()
            logger.info("MIGRATION  pgvector HNSW indexes verified")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("═══ APP STARTUP ═══  title=%s  version=%s", app.title, app.version)
    Base.metadata.create_all(bind=engine)
    logger.info("DB TABLES CREATED/VERIFIED")
    _run_migrations()
    create_views()
    routes = [f"{list(r.methods)} {r.path}" for r in app.routes if hasattr(r, "methods")]
    logger.info("ROUTES REGISTERED  count=%d  %s", len(routes), routes)
    yield
    logger.info("═══ APP SHUTDOWN ═══")


app = FastAPI(
    title="ARIA — Restaurant Inventory Assistant",
    description="AI-powered conversational inventory management",
    version="1.0.0",
    lifespan=lifespan,
)


# ── HTTP request/response logging middleware ───────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Assign a fresh trace ID for this HTTP request — flows into all downstream logs
    tid = new_trace()
    t0 = time.perf_counter()

    # Read body for logging. Starlette's _CachedRequest caches it automatically;
    # call_next's wrapped_receive returns the cached copy to the route handler.
    body_bytes = await request.body()
    body_preview = ""
    if body_bytes:
        try:
            parsed = json.loads(body_bytes)
            if "audio" in parsed:
                parsed["audio"] = f"<{len(body_bytes)} bytes>"
            body_preview = J(parsed, max_len=300)
        except Exception:
            body_preview = f"<binary {len(body_bytes)} bytes>"

    logger.info(separator(f"REQUEST {tid}"))
    logger.info(
        "HTTP ──▶  %s %s  client=%s  body=%s",
        request.method, request.url.path,
        getattr(request.client, "host", "?"),
        body_preview or "(empty)",
    )

    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    logger.info(
        "HTTP ◀──  %s %s  status=%d  elapsed=%.1fms",
        request.method, request.url.path,
        response.status_code, elapsed_ms,
    )
    logger.info(separator(f"END {tid}"))
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory.router)
app.include_router(voice.router)
app.include_router(analytics.router)
app.include_router(conversations.router)
app.include_router(locations.router)
app.include_router(users.router)
app.include_router(chat_router.router)

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/chat", StaticFiles(directory=str(_static_dir), html=True), name="chat-ui")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("WS MESSAGE  data=%s", data[:200])
            await manager.broadcast({"type": "ping", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/")
def root():
    logger.info("HEALTH  endpoint=/ status=running")
    return {"name": "ARIA Inventory Assistant", "status": "running", "docs": "/docs"}


@app.get("/health")
def health():
    logger.debug("HEALTH  endpoint=/health")
    return {"status": "healthy"}
