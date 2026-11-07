from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Site
from app.schemas import SiteOut

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("/", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db)):
    return db.query(Site).all()
