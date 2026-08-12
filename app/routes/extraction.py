<<<<<<< HEAD
import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status

from app.ai.extraction import async_analyze_single_drawing
from app.ai.schemas import (
    AnalysisCallbackResponse,
    AnalysisFailureCallback,
    AnalysisSummary,
    BatchAcceptedResponse,
    BatchExtractionRequest,
    BatchFileRequest,
    BomItem,
    DrawingExtractionRequest,
)


router = APIRouter(tags=["drawing-extraction"])
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff"}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png",
    "application/pdf": ".pdf", "image/tiff": ".tiff",
}
MATERIAL_RATES_PER_KG = {
    "CRCA": 76.58, "MS": 72.0, "SS304": 242.0,
    "SS316": 310.0, "ALUMINIUM": 220.0, "ALUMINUM": 220.0,
}


def _authorize_backend(api_key: str | None) -> None:
    expected_key = os.getenv("AI_API_KEY")
    if expected_key and api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing AI API key.")


def _resolve_filename(file_url: str, supplied_filename: str | None, content_type: str) -> str:
    filename = Path(supplied_filename or "").name
    if not filename:
        filename = Path(unquote(urlparse(file_url).path)).name
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        extension = CONTENT_TYPE_EXTENSIONS.get(content_type)
        if not extension:
            raise HTTPException(status_code=415, detail="Supported formats are JPG, PNG, PDF, TIF, and TIFF.")
        filename = f"drawing{extension}"
    return filename


async def _download_drawing(url: str) -> tuple[bytes, str]:
    max_bytes = int(os.getenv("MAX_DRAWING_BYTES", str(25 * 1024 * 1024)))
    timeout = float(os.getenv("DRAWING_DOWNLOAD_TIMEOUT_SECONDS", "30"))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise HTTPException(status_code=413, detail="Drawing exceeds the configured size limit.")
                chunks, downloaded = [], 0
                async for chunk in response.aiter_bytes():
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise HTTPException(status_code=413, detail="Drawing exceeds the configured size limit.")
                    chunks.append(chunk)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Unable to download drawing: {exc}") from exc
    if not chunks:
        raise HTTPException(status_code=422, detail="The drawing URL returned an empty file.")
    return b"".join(chunks), content_type


def _material_rate(material: str) -> float:
    normalized = material.upper().replace(" ", "")
    return MATERIAL_RATES_PER_KG.get(normalized, float(os.getenv("DEFAULT_MATERIAL_RATE_PER_KG", "100")))


def _to_callback(job_id: str, analysis_id: int, drawing: dict) -> AnalysisCallbackResponse:
    components = drawing.get("components", [])
    primary = components[0] if components else {}
    net_weight = round(sum(c.get("net_weight_kg", 0.0) * c.get("per_set_qty", 1) for c in components), 3)
    gross_weight = round(sum(c.get("gross_weight_kg", 0.0) * c.get("per_set_qty", 1) for c in components), 3)
    scrap_weight = round(max(0.0, gross_weight - net_weight), 3)
    material = primary.get("material", "UNKNOWN")
    rate = _material_rate(material)
    usage = round((net_weight / gross_weight) * 100, 2) if gross_weight else 0.0
    operation_count = sum(1 + c.get("bends", 0) + c.get("slots_count", 0) for c in components)
    machine_time = max(1, round(operation_count * float(os.getenv("SECONDS_PER_OPERATION", "5"))))

    bom = []
    for component in components:
        weight = round(component.get("net_weight_kg", 0.0) * component.get("per_set_qty", 1), 3)
        unit_rate = _material_rate(component.get("material", material))
        bom.append(BomItem(
            item_name=component.get("description") or component.get("part_number", "UNKNOWN"),
            item_class=component.get("component_type", "UNKNOWN").replace("_", " ").title(),
            quantity=weight,
            weight_kg=weight,
            unit_cost=round(unit_rate, 2),
            total_cost=round(weight * unit_rate, 2),
        ))

    return AnalysisCallbackResponse(
        job_id=job_id,
        analysis_id=analysis_id,
        analysis=AnalysisSummary(
            part_number=primary.get("part_number") or drawing.get("drawing_id", "UNKNOWN"),
            part_description=primary.get("description", ""),
            part_type=primary.get("component_type", "UNKNOWN").upper(),
            material=material,
            thickness=primary.get("thickness_mm", 0.0),
            width=primary.get("width_mm", 0.0),
            height=primary.get("height_mm", 0.0),
            length=primary.get("length_mm", 0.0),
            drawing_weight_kg=round(drawing.get("target_blueprint_weight_kg", net_weight), 3),
            calculated_weight_kg=net_weight,
            machine_time_seconds=machine_time,
            sheet_usage_percent=usage,
            material_cost=round(net_weight * rate, 2),
            net_material_weight_kg=net_weight,
            scrap_weight_kg=scrap_weight,
            scrap_cost=round(scrap_weight * rate, 2),
        ),
        bom=bom,
    )


async def _send_callback(callback_url: str, payload: dict) -> None:
    timeout = float(os.getenv("CALLBACK_TIMEOUT_SECONDS", "20"))
    attempts = int(os.getenv("CALLBACK_MAX_ATTEMPTS", "3"))
    headers = {"Content-Type": "application/json"}
    if callback_key := os.getenv("BACKEND_CALLBACK_API_KEY"):
        headers["X-API-Key"] = callback_key
    last_error = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for attempt in range(attempts):
            try:
                response = await client.post(callback_url, json=payload, headers=headers)
                response.raise_for_status()
                return
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(2 ** attempt)
    print(f"[CALLBACK FAILED] {payload.get('job_id')}/{payload.get('analysis_id')}: {last_error}")


async def _process_file(job_id: str, callback_url: str, file: BatchFileRequest, semaphore: asyncio.Semaphore) -> None:
    try:
        async with semaphore:
            file_bytes, content_type = await _download_drawing(str(file.file_url))
            filename = _resolve_filename(str(file.file_url), file.filename, content_type)
            drawing = await async_analyze_single_drawing(file_bytes, filename)
            payload = _to_callback(job_id, file.analysis_id, drawing).model_dump(mode="json")
    except Exception as exc:
        payload = AnalysisFailureCallback(job_id=job_id, analysis_id=file.analysis_id, error=str(exc)).model_dump(mode="json")
    await _send_callback(callback_url, payload)


async def _process_batch(request: BatchExtractionRequest) -> None:
    semaphore = asyncio.Semaphore(max(1, int(os.getenv("AI_BATCH_CONCURRENCY", "3"))))
    await asyncio.gather(*(
        _process_file(request.job_id, str(request.callback_url), file, semaphore)
        for file in request.files
    ))


@router.post("/process-drawings", response_model=BatchAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def process_drawings(request: BatchExtractionRequest, background_tasks: BackgroundTasks, x_api_key: str | None = Header(default=None)):
    """Accept a backend job and callback once for every supplied analysis_id."""
    _authorize_backend(x_api_key)
    analysis_ids = [file.analysis_id for file in request.files]
    if len(analysis_ids) != len(set(analysis_ids)):
        raise HTTPException(status_code=422, detail="analysis_id values must be unique within a job.")
    background_tasks.add_task(_process_batch, request)
    return BatchAcceptedResponse(job_id=request.job_id, file_count=len(request.files))


@router.post("/extract-drawing", response_model=AnalysisCallbackResponse)
async def extract_drawing(request: DrawingExtractionRequest, x_api_key: str | None = Header(default=None)):
    """Synchronous single-file endpoint retained for direct testing."""
    _authorize_backend(x_api_key)
    file_bytes, content_type = await _download_drawing(str(request.image_url))
    filename = _resolve_filename(str(request.image_url), request.filename, content_type)
    drawing = await async_analyze_single_drawing(file_bytes, filename)
    return _to_callback(request.job_id or uuid.uuid4().hex, 0, drawing)
=======
import traceback
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Request
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import storage
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.ai.extraction import async_analyze_single_drawing
from app.diagnostics import cloud_print


router = APIRouter(prefix="/extractions", tags=["extractions"])

ALLOWED_FILE_EXTENSIONS = {".jpeg", ".jpg", ".pdf", ".png", ".tif", ".tiff"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024


class GCSExtractionRequest(BaseModel):
    gcs_uri: str = Field(
        description="Cloud Storage object URI in gs://bucket/path/to/file format."
    )


def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str, str]:
    parsed_uri = urlparse(gcs_uri)

    if parsed_uri.scheme != "gs" or not parsed_uri.netloc:
        raise HTTPException(
            status_code=422,
            detail="gcs_uri must use the gs://bucket/path/to/file format.",
        )

    object_name = unquote(parsed_uri.path.lstrip("/"))
    filename = PurePosixPath(object_name).name
    file_extension = PurePosixPath(filename).suffix.lower()

    if not object_name or not filename:
        raise HTTPException(status_code=422, detail="gcs_uri must reference an object.")

    if file_extension not in ALLOWED_FILE_EXTENSIONS:
        allowed_extensions = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported drawing type. Allowed extensions: {allowed_extensions}.",
        )

    return parsed_uri.netloc, object_name, filename


def _download_gcs_object(
    bucket_name: str,
    object_name: str,
) -> bytes:
    storage_client = storage.Client()
    blob = storage_client.bucket(bucket_name).blob(object_name)

    try:
        blob.reload()
        if blob.size is not None and blob.size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Drawing exceeds the 25 MB extraction limit.",
            )

        return blob.download_as_bytes()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Drawing object was not found.") from exc
    except GoogleAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to read the drawing from Cloud Storage.",
        ) from exc


@router.post("/from-gcs")
async def extract_drawing_from_gcs(
    payload: GCSExtractionRequest,
    request: Request,
):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    trace_id = getattr(request.state, "trace_id", None)
    started_at = time.monotonic()
    bucket_name = None
    object_name = None
    pipeline_stage = "validate_gcs_uri"
    pipeline_step = "parse_gcs_uri"

    cloud_print(
        "EXTRACTION_START",
        message="EXTRACTION START | POST /extractions/from-gcs",
        request_id=request_id,
        http={
            "method": request.method,
            "path": request.url.path,
        },
        input={
            "request_source": "gcs",
        },
        pipeline={
            "stage": pipeline_stage,
            "step": pipeline_step,
        },
        trace_id=trace_id,
    )

    try:
        bucket_name, object_name, filename = _parse_gcs_uri(
            payload.gcs_uri,
        )

        pipeline_stage = "cloud_storage_download"
        pipeline_step = "download_gcs_object"
        file_bytes = await run_in_threadpool(
            _download_gcs_object,
            bucket_name,
            object_name,
        )

        if not file_bytes:
            raise HTTPException(status_code=422, detail="Drawing object is empty.")

        pipeline_stage = "vertex_ai_extraction"
        pipeline_step = "async_analyze_single_drawing"
        result = await async_analyze_single_drawing(
            file_bytes=file_bytes,
            filename=filename,
        )
        return result
    except HTTPException as exc:
        duration_ms = round((time.monotonic() - started_at) * 1000)
        cloud_print(
            "REQUEST_FAILED",
            severity="WARNING" if exc.status_code < 500 else "ERROR",
            message=(
                f"REQUEST FAILED | POST /extractions/from-gcs | "
                f"HTTPException: {exc.detail}"
            ),
            request_id=request_id,
            http={
                "method": request.method,
                "path": request.url.path,
                "status": exc.status_code,
                "duration_ms": duration_ms,
            },
            input={
                "bucket": bucket_name,
                "object": object_name,
                "request_source": "gcs",
            },
            pipeline={
                "stage": pipeline_stage,
                "step": pipeline_step,
            },
            exception={
                "type": type(exc).__name__,
                "message": str(exc.detail),
            },
            trace_id=trace_id,
        )
        raise
    except Exception as exc:
        duration_ms = round((time.monotonic() - started_at) * 1000)
        cloud_print(
            "REQUEST_FAILED",
            severity="ERROR",
            message=(
                f"REQUEST FAILED | POST /extractions/from-gcs | "
                f"{type(exc).__name__}: {exc}"
            ),
            request_id=request_id,
            http={
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "duration_ms": duration_ms,
            },
            input={
                "bucket": bucket_name,
                "object": object_name,
                "request_source": "gcs",
            },
            pipeline={
                "stage": pipeline_stage,
                "step": pipeline_step,
            },
            exception={
                "type": type(exc).__name__,
                "message": str(exc),
            },
            trace_id=trace_id,
        )
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Extraction failed. Reference: {request_id}",
        ) from exc
>>>>>>> 02d4e824a07698b02789b94c187f806cabe0962f
