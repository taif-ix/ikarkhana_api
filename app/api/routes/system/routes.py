from __future__ import annotations

import os

from fastapi import APIRouter

from app.models.schemas import GeminiConfig
from app.services.vision import package_installed


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/")
def api_root() -> dict[str, object]:
    return {
        "service": "cost_estimator_api",
        "status": "ok",
        "frontend_repo": "https://github.com/taif-ix/ikarkhana_web",
        "docs": "/docs",
        "endpoints": [
            "/health",
            "/gemini-config",
            "/expand-upload",
            "/batch-process/start",
            "/batch-process/{job_id}",
            "/batch-process/{job_id}/retry",
            "/diagram-preview",
            "/extract-dimensions",
            "/extract-references",
            "/batch-extract-references",
            "/extract-structured",
            "/extract-cost-breakdown",
            "/calculate-cost-breakdown",
            "/estimate",
        ],
    }


@router.get("/gemini-config", response_model=GeminiConfig)
def gemini_config() -> GeminiConfig:
    provider = os.getenv("GEMINI_PROVIDER", "gemini_api").lower()
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    api_key = os.getenv("GEMINI_API_KEY")
    return GeminiConfig(
        provider="vertex_ai" if provider == "vertex_ai" else "gemini_api",
        api_key_configured=bool(api_key and api_key != "your-gemini-api-key"),
        project_configured=bool(project and project != "your-gcp-project-id"),
        project=project,
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1"),
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        google_genai_installed=package_installed("google.genai"),
        pillow_installed=package_installed("PIL"),
    )


@router.get("/vertex-config", response_model=GeminiConfig)
def vertex_config() -> GeminiConfig:
    return gemini_config()
