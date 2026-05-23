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
from database import engine, Base, SessionLocal
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


def _run_migrations():
    """Automatically add any missing columns to existing tables."""
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
        with engine.connect() as conn:
            for col, definition in migrations:
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE inventory ADD COLUMN {col} {definition}"))
                    logger.info("MIGRATION  added column inventory.%s (%s)", col, definition)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("═══ APP STARTUP ═══  title=%s  version=%s", app.title, app.version)
    Base.metadata.create_all(bind=engine)
    logger.info("DB TABLES CREATED/VERIFIED")
    _run_migrations()
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

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]

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
