from __future__ import annotations

import base64
import os

from fastapi import UploadFile
from pydantic import BaseModel

from app.core.config import (
    LABOR_TACKING_FIXED,
    LABOR_WELDING_PER_METER,
    RATE_PER_BEND_STROKE,
    RATE_PER_CUT_METER,
    RATE_PER_KG,
    RATE_PER_PRESS_MACHINE_HIT,
    RATE_PER_SQ_METER_PAINT,
)
from app.models.schemas import StructuredExtraction


ALLOWED_UPLOAD_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pdf", ".dwg"}


class StructuredCostRequest(BaseModel):
    extraction: StructuredExtraction
    material_rate_per_kg: float | None = RATE_PER_KG
    laser_cutting_rate_per_meter: float = RATE_PER_CUT_METER
    press_machine_rate_per_hit: float = RATE_PER_PRESS_MACHINE_HIT
    bend_rate_per_bend: float = RATE_PER_BEND_STROKE
    welding_labor_per_meter: float = LABOR_WELDING_PER_METER
    painting_rate_per_m2: float = RATE_PER_SQ_METER_PAINT
    scrap_rate_per_kg: float = 28.0
    tacking_fixed_setup_cost: float = LABOR_TACKING_FIXED


def data_url(content: bytes, mime_type: str | None) -> str:
    mime = mime_type or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def drawing_base(name: str) -> str:
    return os.path.splitext(os.path.basename(name or ""))[0].strip().lower()


def file_matches_hint(filename: str, hint: str) -> bool:
    return drawing_base(filename) == drawing_base(hint)


async def read_child_drawings(child_diagrams: list[UploadFile] | None) -> list[tuple[str, bytes, str | None]]:
    drawings: list[tuple[str, bytes, str | None]] = []
    for child in child_diagrams or []:
        drawings.append((child.filename or "child-detail-drawing", await child.read(), child.content_type))
    return drawings
