from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Location, StorageArea
from schemas import LocationCreate, LocationResponse, StorageAreaCreate, StorageAreaResponse

router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get("/", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    return db.query(Location).order_by(Location.name).all()


@router.post("/", response_model=LocationResponse)
def create_location(data: LocationCreate, db: Session = Depends(get_db)):
    existing = db.query(Location).filter(Location.name.ilike(data.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Location already exists")
    loc = Location(name=data.name, description=data.description)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()
    return {"message": "Location deleted"}


@router.get("/{location_id}/storage-areas", response_model=List[StorageAreaResponse])
def get_storage_areas(location_id: int, db: Session = Depends(get_db)):
    return (
        db.query(StorageArea)
        .filter(StorageArea.location_id == location_id)
        .order_by(StorageArea.name)
        .all()
    )


@router.post("/{location_id}/storage-areas", response_model=StorageAreaResponse)
def create_storage_area(location_id: int, data: StorageAreaCreate, db: Session = Depends(get_db)):
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    existing = (
        db.query(StorageArea)
        .filter(StorageArea.location_id == location_id, StorageArea.name.ilike(data.name))
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Storage area already exists in this location")
    area = StorageArea(location_id=location_id, name=data.name, description=data.description)
    db.add(area)
    db.commit()
    db.refresh(area)
    return area


@router.delete("/storage-areas/{area_id}")
def delete_storage_area(area_id: int, db: Session = Depends(get_db)):
    area = db.query(StorageArea).filter(StorageArea.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Storage area not found")
    db.delete(area)
    db.commit()
    return {"message": "Storage area deleted"}
