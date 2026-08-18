import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google import genai

from app import state
from app.diagnostics import cloud_print
from app.routes.batch import router as batch_router
from app.routes.extraction import router as extraction_router
from app.routes.workspace import router as workspace_router
from app.routes.uploads import router as uploads_router
from app.routes.structured_costing import router as structured_costing_router


load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Use Gemini API when a key is configured; otherwise use Vertex AI credentials.
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-automobile-product-costing")
    location_id = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    api_key = os.getenv("GEMINI_API_KEY")

    print(f"[SYSTEM START]: Initializing Calibrated GenAI Engine Pipeline: {project_id}")
    if api_key and api_key != "your-gemini-api-key":
        state.client = genai.Client(api_key=api_key)
    else:
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

    @api.exception_handler(Exception)
    async def unhandled_exception_response(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
                "request_id": request_id,
            },
        )

    @api.middleware("http")
    async def print_request_details(request: Request, call_next):
        request_id = uuid.uuid4().hex
        started_at = time.monotonic()
        trace_id = request.headers.get("X-Cloud-Trace-Context", "").split("/", 1)[0] or None
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        cloud_print(
            "REQUEST_START",
            message=f"REQUEST START | {request.method} {request.url.path}",
            request_id=request_id,
            http={
                "method": request.method,
                "path": request.url.path,
            },
            trace_id=trace_id,
        )

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            endpoint_function = request.scope.get("endpoint")
            function_name = getattr(endpoint_function, "__name__", "unknown")
            duration_ms = round((time.monotonic() - started_at) * 1000)

            cloud_print(
                "REQUEST_FAILED",
                severity="ERROR",
                message=(
                    f"REQUEST FAILED | {request.method} {request.url.path} | "
                    f"{type(exc).__name__}: {exc}"
                ),
                request_id=request_id,
                endpoint_function=function_name,
                http={
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": duration_ms,
                },
                pipeline={
                    "stage": "request_middleware",
                    "step": "call_endpoint",
                },
                exception={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                trace_id=trace_id,
            )
            traceback.print_exc()
            raise

    api.include_router(workspace_router)
    api.include_router(extraction_router)
    api.include_router(batch_router)
    api.include_router(uploads_router)
    api.include_router(structured_costing_router)
    return api


app = create_app()
