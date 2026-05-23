import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import Location, StorageArea
from schemas import LocationCreate, LocationResponse, StorageAreaCreate, StorageAreaResponse

router = APIRouter(prefix="/api/locations", tags=["locations"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    locations = db.query(Location).order_by(Location.name).all()
    logger.info("LOCATIONS GET  count=%d", len(locations))
    return locations


@router.post("/", response_model=LocationResponse)
def create_location(data: LocationCreate, db: Session = Depends(get_db)):
    logger.info("LOCATIONS CREATE  name=%r  description=%r", data.name, data.description)
    existing = db.query(Location).filter(Location.name.ilike(data.name)).first()
    if existing:
        logger.warning("LOCATIONS CREATE  CONFLICT  name=%r already exists", data.name)
        raise HTTPException(status_code=400, detail="Location already exists")
    loc = Location(name=data.name, description=data.description)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    logger.info("LOCATIONS CREATED  id=%d  name=%r", loc.id, loc.name)
    return loc


@router.delete("/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    logger.warning("LOCATIONS DELETE  location_id=%d", location_id)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        logger.warning("LOCATIONS DELETE  id=%d NOT FOUND", location_id)
        raise HTTPException(status_code=404, detail="Location not found")
    logger.warning("LOCATIONS DELETED  id=%d  name=%r", location_id, loc.name)
    db.delete(loc)
    db.commit()
    return {"message": "Location deleted"}


@router.get("/{location_id}/storage-areas", response_model=List[StorageAreaResponse])
def get_storage_areas(location_id: int, db: Session = Depends(get_db)):
    areas = (
        db.query(StorageArea)
        .filter(StorageArea.location_id == location_id)
        .order_by(StorageArea.name)
        .all()
    )
    logger.info("STORAGE-AREAS GET  location_id=%d  count=%d", location_id, len(areas))
    return areas


@router.post("/{location_id}/storage-areas", response_model=StorageAreaResponse)
def create_storage_area(location_id: int, data: StorageAreaCreate, db: Session = Depends(get_db)):
    logger.info("STORAGE-AREAS CREATE  location_id=%d  name=%r", location_id, data.name)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    existing = (
        db.query(StorageArea)
        .filter(StorageArea.location_id == location_id, StorageArea.name.ilike(data.name))
        .first()
    )
    if existing:
        logger.warning("STORAGE-AREAS CREATE  CONFLICT  location_id=%d  name=%r", location_id, data.name)
        raise HTTPException(status_code=400, detail="Storage area already exists in this location")
    area = StorageArea(location_id=location_id, name=data.name, description=data.description)
    db.add(area)
    db.commit()
    db.refresh(area)
    logger.info("STORAGE-AREAS CREATED  id=%d  name=%r  location=%r", area.id, area.name, loc.name)
    return area


@router.delete("/storage-areas/{area_id}")
def delete_storage_area(area_id: int, db: Session = Depends(get_db)):
    logger.warning("STORAGE-AREAS DELETE  area_id=%d", area_id)
    area = db.query(StorageArea).filter(StorageArea.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Storage area not found")
    logger.warning("STORAGE-AREAS DELETED  id=%d  name=%r", area_id, area.name)
    db.delete(area)
    db.commit()
    return {"message": "Storage area deleted"}
