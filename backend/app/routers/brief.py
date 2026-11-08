from fastapi import APIRouter

from app.services.llm_brain import generate_brief

router = APIRouter(prefix="/brief", tags=["llm-brain"])


@router.post("/")
def post_brief(risk: dict):
    """Takes a risk-model output dict, returns LLM-generated summary + dispatch message."""
    return generate_brief(risk)
