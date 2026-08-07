import os
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
    debug_failure: bool = False,
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
        # Temporary dev-only switch for verifying structured Cloud Run failures.
        if debug_failure:
            if not os.getenv("K_SERVICE", "").endswith("-dev"):
                raise HTTPException(status_code=404, detail="Not found.")

            pipeline_stage = "debug_failure"
            pipeline_step = "raise_test_exception"
            raise RuntimeError("Intentional dev failure for Cloud Logging verification.")

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
