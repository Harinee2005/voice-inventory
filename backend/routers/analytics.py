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


@router.get("/", response_model=AnalyticsResponse)
def get_analytics(db: Session = Depends(get_db)):
    total_items = db.query(InventoryItem).count()
    category_summary = get_category_summary(db)
    return AnalyticsResponse(
        total_items=total_items,
        total_categories=len(category_summary),
        low_stock_items=get_low_stock_items(db),
        category_summary=category_summary,
        expiring_soon=get_expiring_soon(db),
        recent_activity=get_recent_activity(db),
        flagged_items=get_flagged_items(db),
        price_analytics=get_price_analytics(db),
    )


@router.get("/expiring")
def get_expiring_items(days: int = 3, db: Session = Depends(get_db)):
    return {"items": get_expiring_soon(db, days)}


@router.get("/low-stock")
def get_low_stock(db: Session = Depends(get_db)):
    return {"items": get_low_stock_items(db)}
