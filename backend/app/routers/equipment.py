from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Equipment
from app.schemas import EquipmentOut

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.get("/", response_model=list[EquipmentOut])
def list_equipment(db: Session = Depends(get_db)):
    return db.query(Equipment).all()


@router.get("/{equipment_id}", response_model=EquipmentOut)
def get_equipment(equipment_id: int, db: Session = Depends(get_db)):
    return db.query(Equipment).filter(Equipment.id == equipment_id).first()
