import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import InventoryItem
from schemas import AnalyticsResponse
from services.inventory_service import (
    get_low_stock_items,
    get_expiring_soon,
    get_category_summary,
    get_flagged_items,
    get_recent_activity,
    get_price_analytics,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=AnalyticsResponse)
def get_analytics(db: Session = Depends(get_db)):
    logger.info("ANALYTICS GET  full analytics requested")
    total_items = db.query(InventoryItem).count()
    category_summary = get_category_summary(db)
    low_stock = get_low_stock_items(db)
    expiring = get_expiring_soon(db)
    flagged = get_flagged_items(db)
    recent = get_recent_activity(db)
    price = get_price_analytics(db)
    logger.info(
        "ANALYTICS DONE  total_items=%d  categories=%d  low_stock=%d  "
        "expiring=%d  flagged=%d  recent_activity=%d  total_value=%.2f",
        total_items, len(category_summary), len(low_stock),
        len(expiring), len(flagged), len(recent),
        price.get("total_value", 0),
    )
    return AnalyticsResponse(
        total_items=total_items,
        total_categories=len(category_summary),
        low_stock_items=low_stock,
        category_summary=category_summary,
        expiring_soon=expiring,
        recent_activity=recent,
        flagged_items=flagged,
        price_analytics=price,
    )


@router.get("/expiring")
def get_expiring_items(days: int = 3, db: Session = Depends(get_db)):
    logger.info("ANALYTICS /expiring  days=%d", days)
    items = get_expiring_soon(db, days)
    logger.info("ANALYTICS /expiring  found=%d", len(items))
    return {"items": items}


@router.get("/low-stock")
def get_low_stock(db: Session = Depends(get_db)):
    logger.info("ANALYTICS /low-stock")
    items = get_low_stock_items(db)
    logger.info("ANALYTICS /low-stock  found=%d", len(items))
    return {"items": items}
