import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai

from app import state
from app.routes.batch import router as batch_router
from app.routes.extraction import router as extraction_router
from app.routes.workspace import router as workspace_router


load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the shared Vertex AI/Gemini client once at startup.
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-automobile-product-costing")
    location_id = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    print(f"[SYSTEM START]: Initializing Calibrated GenAI Engine Pipeline: {project_id}")
    state.client = genai.Client(
        vertexai=True,
        http_options={"api_version": "v1", "headers": {"x-goog-user-project": project_id}},
        project=project_id,
        location=location_id,
    )
    yield
    state.session_cache.clear()
    state.GLOBAL_BLUEPRINT_CACHE.clear()
    print("[SYSTEM STOP]: Cache and visual workspaces successfully decoupled.")


def create_app() -> FastAPI:
    # Build the FastAPI app and attach all route groups.
    api = FastAPI(title="Industrial Smart Stamping & Visual Costing Engine", lifespan=lifespan)
    api.include_router(workspace_router)
    api.include_router(extraction_router)
    api.include_router(batch_router)
    return api


app = create_app()
