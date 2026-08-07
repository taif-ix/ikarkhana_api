from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import storage
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.ai.extraction import async_analyze_single_drawing


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


def _download_gcs_object(bucket_name: str, object_name: str) -> bytes:
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
async def extract_drawing_from_gcs(request: GCSExtractionRequest):
    bucket_name, object_name, filename = _parse_gcs_uri(request.gcs_uri)
    file_bytes = await run_in_threadpool(
        _download_gcs_object,
        bucket_name,
        object_name,
    )

    if not file_bytes:
        raise HTTPException(status_code=422, detail="Drawing object is empty.")

    return await async_analyze_single_drawing(
        file_bytes=file_bytes,
        filename=filename,
    )
