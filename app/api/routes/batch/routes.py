from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.routes.common import data_url, drawing_base, file_matches_hint
from app.core.config import (
    LABOR_TACKING_FIXED,
    LABOR_WELDING_PER_METER,
    RATE_PER_BEND_STROKE,
    RATE_PER_CUT_METER,
    RATE_PER_KG,
    RATE_PER_PRESS_MACHINE_HIT,
    RATE_PER_SQ_METER_PAINT,
)
from app.models.schemas import BatchReferenceExtraction, BatchReferenceItem
from app.services.estimator import calculate_structured_cost_breakdown
from app.services.vision import extract_references_with_gemini, extract_structured_with_gemini, image_bytes_for_preview


router = APIRouter()

BATCH_PROCESS_JOBS: dict[str, dict[str, Any]] = {}
BATCH_PROCESS_CONCURRENCY = max(int(os.getenv("BATCH_PROCESS_CONCURRENCY", "4")), 1)


def _public_batch_job(job: dict[str, Any]) -> dict[str, Any]:
    public_files: list[dict[str, Any]] = []
    for file_record in job.get("files", []):
        public_files.append(
            {
                key: value
                for key, value in file_record.items()
                if not key.startswith("_")
            }
        )
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "concurrency": job["concurrency"],
        "files": public_files,
    }


def _touch_job(job: dict[str, Any]) -> None:
    job["updated_at"] = time.time()


async def _run_single_batch_file(job_id: str, file_record: dict[str, Any]) -> None:
    job = BATCH_PROCESS_JOBS[job_id]
    file_record["status"] = "processing"
    file_record["error"] = None
    _touch_job(job)

    hints = [str(hint) for hint in file_record.get("child_hints", [])]
    child_files = [
        other
        for other in job["files"]
        if other["file_name"] != file_record["file_name"]
        and any(file_matches_hint(other["file_name"], hint) for hint in hints)
    ]
    child_drawings = [
        (child["file_name"], child["_content"], child.get("_content_type"))
        for child in child_files
    ]
    file_record["child_files"] = [
        {
            "file_name": child["file_name"],
            "size_mb": child["size_mb"],
            "preview_image": child["preview_image"],
        }
        for child in child_files
    ]
    file_record["missing_child_hints"] = [
        hint
        for hint in hints
        if not any(file_matches_hint(child["file_name"], hint) for child in child_files)
    ]

    try:
        extraction = await asyncio.to_thread(
            extract_structured_with_gemini,
            file_record["_content"],
            file_record.get("_content_type"),
            child_drawings,
        )
        breakdown = await asyncio.to_thread(
            calculate_structured_cost_breakdown,
            extraction,
            material_rate_per_kg=RATE_PER_KG,
            laser_cutting_rate_per_meter=RATE_PER_CUT_METER,
            press_machine_rate_per_hit=RATE_PER_PRESS_MACHINE_HIT,
            bend_rate_per_bend=RATE_PER_BEND_STROKE,
            welding_labor_per_meter=LABOR_WELDING_PER_METER,
            painting_rate_per_m2=RATE_PER_SQ_METER_PAINT,
            scrap_rate_per_kg=28.0,
            tacking_fixed_setup_cost=LABOR_TACKING_FIXED,
        )
        file_record["status"] = "processed"
        file_record["structured_extraction"] = extraction.model_dump(mode="json")
        file_record["structured_breakdown"] = breakdown.model_dump(mode="json")
        file_record["part_count"] = len(breakdown.per_part_breakdown)
    except Exception as exc:
        file_record["status"] = "error"
        file_record["error"] = str(exc)
    finally:
        _touch_job(job)


async def _run_batch_job(job_id: str) -> None:
    job = BATCH_PROCESS_JOBS[job_id]
    job["status"] = "processing"
    _touch_job(job)
    semaphore = asyncio.Semaphore(BATCH_PROCESS_CONCURRENCY)

    async def run_with_limit(file_record: dict[str, Any]) -> None:
        async with semaphore:
            await _run_single_batch_file(job_id, file_record)

    await asyncio.gather(*(run_with_limit(file_record) for file_record in job["files"]))
    statuses = {file_record["status"] for file_record in job["files"]}
    job["status"] = "complete" if "processing" not in statuses and "queued" not in statuses else "processing"
    if statuses == {"error"}:
        job["status"] = "error"
    _touch_job(job)


@router.post("/batch-process/start")
async def start_batch_process(
    diagrams: list[UploadFile] = File(...),
    dependency_hints_json: str = Form("{}"),
) -> dict[str, Any]:
    try:
        dependency_hints = json.loads(dependency_hints_json or "{}")
        if not isinstance(dependency_hints, dict):
            dependency_hints = {}
    except json.JSONDecodeError:
        dependency_hints = {}

    files: list[dict[str, Any]] = []
    for diagram in diagrams:
        filename = diagram.filename or "uploaded-drawing"
        content = await diagram.read()
        content_type = diagram.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        preview_content, preview_mime = image_bytes_for_preview(content, content_type, filename)
        hints = dependency_hints.get(drawing_base(filename), [])
        if not isinstance(hints, list):
            hints = []
        files.append(
            {
                "file_name": filename,
                "size_kb": round(len(content) / 1024, 2),
                "size_mb": f"{len(content) / (1024 * 1024):.2f} MB",
                "content_type": preview_mime,
                "preview_image": data_url(preview_content, preview_mime),
                "status": "queued",
                "error": None,
                "child_hints": [str(hint) for hint in hints],
                "missing_child_hints": [],
                "child_files": [],
                "part_count": 0,
                "_content": preview_content,
                "_content_type": preview_mime,
            }
        )

    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one drawing file.")

    job_id = uuid.uuid4().hex
    BATCH_PROCESS_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
        "concurrency": BATCH_PROCESS_CONCURRENCY,
        "files": files,
    }
    asyncio.create_task(_run_batch_job(job_id))
    return _public_batch_job(BATCH_PROCESS_JOBS[job_id])


@router.get("/batch-process/{job_id}")
async def get_batch_process(job_id: str) -> dict[str, Any]:
    job = BATCH_PROCESS_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found.")
    return _public_batch_job(job)


@router.post("/batch-process/{job_id}/retry")
async def retry_batch_process_file(job_id: str, file_name: str = Form(...)) -> dict[str, Any]:
    job = BATCH_PROCESS_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found.")
    file_record = next((item for item in job["files"] if item["file_name"] == file_name), None)
    if not file_record:
        raise HTTPException(status_code=404, detail="Batch file not found.")
    if file_record["status"] == "processing":
        return _public_batch_job(job)
    file_record["status"] = "queued"
    file_record["error"] = None
    job["status"] = "processing"
    _touch_job(job)
    asyncio.create_task(_run_single_batch_file(job_id, file_record))
    return _public_batch_job(job)


@router.post("/batch-extract-references", response_model=BatchReferenceExtraction, response_model_exclude_none=True)
async def batch_extract_references(diagrams: list[UploadFile] = File(...)) -> BatchReferenceExtraction:
    async def scan_one(diagram: UploadFile) -> BatchReferenceItem:
        content = await diagram.read()
        file_name = diagram.filename or "uploaded-drawing"
        try:
            extraction = await asyncio.to_thread(extract_references_with_gemini, content, diagram.content_type, file_name)
            return BatchReferenceItem(
                file_name=file_name,
                file_size_kb=round(len(content) / 1024, 2),
                drawing_number=extraction.drawing_number,
                referenced_drawings=extraction.referenced_drawings,
                confidence=extraction.confidence,
                notes=extraction.notes,
            )
        except Exception as exc:
            return BatchReferenceItem(
                file_name=file_name,
                file_size_kb=round(len(content) / 1024, 2),
                referenced_drawings=[],
                confidence=0,
                notes=[f"Reference scan failed for this file: {exc}"],
            )

    semaphore = asyncio.Semaphore(BATCH_PROCESS_CONCURRENCY)

    async def scan_with_limit(diagram: UploadFile) -> BatchReferenceItem:
        async with semaphore:
            return await scan_one(diagram)

    items = await asyncio.gather(*(scan_with_limit(diagram) for diagram in diagrams))
    return BatchReferenceExtraction(files=items)
