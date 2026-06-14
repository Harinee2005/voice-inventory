import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./inventory.db")

# Log the DB URL with credentials masked
_safe_url = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
logger.info("DATABASE  url=...%s", _safe_url)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_views():
    """Create read-only SQL views used by LLM context builders."""
    with engine.connect() as conn:
        if "sqlite" in DATABASE_URL:
            conn.execute(text("""
                CREATE VIEW IF NOT EXISTS v_today_inventory AS
                SELECT id, item_name, category, quantity, unit,
                       storage_area, location_name, expiry_date,
                       updated_by, unit_price, is_flagged, count_date, timestamp
                FROM inventory
            """))
            conn.execute(text("""
                CREATE VIEW IF NOT EXISTS v_item_history AS
                SELECT i.id, i.item_name, i.unit, i.unit_price,
                       i.storage_area, i.location_name, i.count_date, i.quantity
                FROM inventory i
                INNER JOIN (
                    SELECT item_name, MAX(timestamp) AS max_ts
                    FROM inventory GROUP BY item_name
                ) latest ON i.item_name = latest.item_name
                         AND i.timestamp = latest.max_ts
                ORDER BY i.item_name
            """))
        else:
            conn.execute(text("""
                CREATE OR REPLACE VIEW v_today_inventory AS
                SELECT id, item_name, category, quantity, unit,
                       storage_area, location_name, expiry_date,
                       updated_by, unit_price, is_flagged, count_date, timestamp
                FROM inventory
            """))
            conn.execute(text("""
                CREATE OR REPLACE VIEW v_item_history AS
                SELECT DISTINCT ON (item_name)
                       id, item_name, unit, unit_price,
                       storage_area, location_name, count_date, quantity
                FROM inventory
                ORDER BY item_name, timestamp DESC
            """))
        conn.commit()
    logger.info("DB VIEWS  v_today_inventory + v_item_history created/verified")
