from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import InventoryItem, ActivityLog


LOW_STOCK_THRESHOLD = 5.0
EXPIRY_WARNING_DAYS = 3


def get_low_stock_items(db: Session) -> list:
    items = db.query(InventoryItem).filter(InventoryItem.quantity <= LOW_STOCK_THRESHOLD).all()
    return [
        {
            "id": i.id,
            "item_name": i.item_name,
            "category": i.category,
            "quantity": i.quantity,
            "unit": i.unit,
            "storage_area": i.storage_area,
            "location_name": i.location_name,
        }
        for i in items
    ]


def get_expiring_soon(db: Session, days: int = EXPIRY_WARNING_DAYS) -> list:
    today = datetime.utcnow().date()
    items = db.query(InventoryItem).filter(InventoryItem.expiry_date.isnot(None)).all()
    expiring = []
    for item in items:
        try:
            exp_date = datetime.strptime(item.expiry_date, "%Y-%m-%d").date()
            days_left = (exp_date - today).days
            if days_left <= days:
                expiring.append({
                    "id": item.id,
                    "item_name": item.item_name,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "expiry_date": item.expiry_date,
                    "days_left": days_left,
                })
        except ValueError:
            continue
    return sorted(expiring, key=lambda x: x["days_left"])


def get_category_summary(db: Session) -> list:
    items = db.query(InventoryItem).all()
    summary: dict = {}
    for item in items:
        cat = item.category
        if cat not in summary:
            summary[cat] = {"category": cat, "item_count": 0, "total_quantity": 0}
        summary[cat]["item_count"] += 1
        summary[cat]["total_quantity"] += item.quantity
    return sorted(summary.values(), key=lambda x: x["item_count"], reverse=True)


def get_flagged_items(db: Session) -> list:
    items = db.query(InventoryItem).filter(InventoryItem.is_flagged == True).all()
    return [
        {
            "id": i.id,
            "item_name": i.item_name,
            "category": i.category,
            "quantity": i.quantity,
            "unit": i.unit,
            "storage_area": i.storage_area,
            "location_name": i.location_name,
            "notes": i.notes,
        }
        for i in items
    ]


def get_price_analytics(db: Session) -> dict:
    items = db.query(InventoryItem).filter(InventoryItem.unit_price.isnot(None)).all()
    valued = [
        {
            "item_name": i.item_name,
            "category": i.category,
            "quantity": i.quantity,
            "unit": i.unit,
            "unit_price": round(i.unit_price, 2),
            "total_value": round(i.quantity * i.unit_price, 2),
        }
        for i in items if i.quantity and i.unit_price
    ]
    valued.sort(key=lambda x: x["total_value"], reverse=True)

    by_cat: dict = {}
    for v in valued:
        cat = v["category"]
        if cat not in by_cat:
            by_cat[cat] = {"category": cat, "total_value": 0.0, "item_count": 0}
        by_cat[cat]["total_value"] += v["total_value"]
        by_cat[cat]["item_count"] += 1
    cat_list = sorted(by_cat.values(), key=lambda x: x["total_value"], reverse=True)
    for c in cat_list:
        c["total_value"] = round(c["total_value"], 2)

    return {
        "total_value": round(sum(v["total_value"] for v in valued), 2),
        "top_items": valued[:6],
        "by_category": cat_list,
    }


def get_recent_activity(db: Session, limit: int = 20) -> list:
    logs = (
        db.query(ActivityLog)
        .order_by(ActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "action": l.action,
            "item_name": l.item_name,
            "details": l.details,
            "worker": l.worker,
            "timestamp": l.timestamp.isoformat(),
        }
        for l in logs
    ]



