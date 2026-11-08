from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.models import models  # noqa: F401 (ensures models are registered)
from app.routers import brief, equipment, sites

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Predictive Maintenance API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sites.router)
app.include_router(equipment.router)
app.include_router(brief.router)


@app.get("/health")
def health():
    return {"status": "ok"}
