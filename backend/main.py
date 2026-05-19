import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text, inspect
from database import engine, Base, SessionLocal
from routers import inventory, voice, analytics, conversations, locations


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

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


manager = ConnectionManager()


def _run_migrations():
    """Automatically add any missing columns to existing tables."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

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
                    print(f"[Migration] Added column inventory.{col}")
            conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    yield


app = FastAPI(
    title="ARIA — Restaurant Inventory Assistant",
    description="AI-powered conversational inventory management",
    version="1.0.0",
    lifespan=lifespan,
)

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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast({"type": "ping", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/")
def root():
    return {
        "name": "ARIA Inventory Assistant",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
