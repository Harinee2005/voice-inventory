import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date as date_type

from database import get_db
from models import InventoryItem
from schemas import InventoryItemCreate, InventoryItemUpdate, InventoryItemResponse

router = APIRouter(prefix="/api/inventory", tags=["inventory"])
logger = logging.getLogger(__name__)


@router.get("/dates/list")
def get_available_dates(db: Session = Depends(get_db)):
    dates = db.query(InventoryItem.count_date).distinct().order_by(InventoryItem.count_date.desc()).all()
    result = [str(d[0]) for d in dates if d[0]]
    logger.info("INVENTORY /dates/list  count=%d", len(result))
    return {"dates": result}


@router.get("/storage-areas/list")
def get_storage_areas_list(db: Session = Depends(get_db)):
    areas = db.query(InventoryItem.storage_area).distinct().all()
    result = [a[0] for a in areas if a[0]]
    logger.info("INVENTORY /storage-areas/list  count=%d  areas=%s", len(result), result)
    return {"storage_areas": result}


@router.get("/categories/list")
def get_categories(db: Session = Depends(get_db)):
    cats = db.query(InventoryItem.category).distinct().all()
    result = [c[0] for c in cats]
    logger.info("INVENTORY /categories/list  count=%d  categories=%s", len(result), result)
    return {"categories": result}


@router.get("/", response_model=List[InventoryItemResponse])
def get_all_inventory(
    category: Optional[str] = None,
    storage_area: Optional[str] = None,
    location_name: Optional[str] = None,
    date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    logger.info(
        "INVENTORY GET  category=%r  storage_area=%r  location=%r  date=%r",
        category, storage_area, location_name, date,
    )
    query = db.query(InventoryItem)

    if date == "all":
        pass
    elif date:
        query = query.filter(InventoryItem.count_date == date)
    else:
        query = query.filter(InventoryItem.count_date == date_type.today())

    if location_name:
        query = query.filter(InventoryItem.location_name == location_name)
    if category:
        query = query.filter(InventoryItem.category == category)
    if storage_area:
        query = query.filter(InventoryItem.storage_area == storage_area)

    items = query.order_by(InventoryItem.category, InventoryItem.item_name).all()
    logger.info("INVENTORY GET  returned=%d items", len(items))
    return items


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(item_id: int, db: Session = Depends(get_db)):
    logger.info("INVENTORY GET item_id=%d", item_id)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        logger.warning("INVENTORY GET  item_id=%d  NOT FOUND", item_id)
        raise HTTPException(status_code=404, detail="Item not found")
    logger.info("INVENTORY GET  item=%r  qty=%s %s  area=%r", item.item_name, item.quantity, item.unit, item.storage_area)
    return item


@router.post("/", response_model=InventoryItemResponse)
def create_inventory_item(item: InventoryItemCreate, db: Session = Depends(get_db)):
    logger.info(
        "INVENTORY CREATE  item=%r  qty=%s  unit=%r  area=%r  cat=%r  worker=%r",
        item.item_name, item.quantity, item.unit, item.storage_area, item.category,
        getattr(item, "updated_by", "?"),
    )
    db_item = InventoryItem(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    logger.info("INVENTORY CREATED  id=%d  item=%r", db_item.id, db_item.item_name)
    return db_item


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: int, update: InventoryItemUpdate, db: Session = Depends(get_db)
):
    logger.info("INVENTORY PATCH  item_id=%d  fields=%s", item_id, update.model_dump(exclude_unset=True))
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        logger.warning("INVENTORY PATCH  item_id=%d  NOT FOUND", item_id)
        raise HTTPException(status_code=404, detail="Item not found")
    changes = update.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    logger.info("INVENTORY UPDATED  item_id=%d  item=%r  changes=%s", item_id, item.item_name, changes)
    return item


@router.delete("/{item_id}")
def delete_inventory_item(item_id: int, db: Session = Depends(get_db)):
    logger.info("INVENTORY DELETE  item_id=%d", item_id)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        logger.warning("INVENTORY DELETE  item_id=%d  NOT FOUND", item_id)
        raise HTTPException(status_code=404, detail="Item not found")
    logger.warning("INVENTORY DELETED  item_id=%d  item=%r  qty=%s %s", item_id, item.item_name, item.quantity, item.unit)
    db.delete(item)
    db.commit()
    return {"message": "Item deleted"}
