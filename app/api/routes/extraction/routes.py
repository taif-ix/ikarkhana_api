from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from app.api.routes.common import read_child_drawings
from app.core.config import (
    LABOR_TACKING_FIXED,
    LABOR_WELDING_PER_METER,
    RATE_PER_BEND_STROKE,
    RATE_PER_CUT_METER,
    RATE_PER_KG,
    RATE_PER_PRESS_MACHINE_HIT,
    RATE_PER_SQ_METER_PAINT,
)
from app.models.schemas import ReferenceExtraction, StructuredCostBreakdown, StructuredExtraction
from app.services.estimator import calculate_structured_cost_breakdown
from app.services.vision import extract_references_with_gemini, extract_structured_with_gemini


router = APIRouter()


@router.post("/extract-references", response_model=ReferenceExtraction, response_model_exclude_none=True)
async def extract_references(diagram: UploadFile = File(...)) -> ReferenceExtraction:
    content = await diagram.read()
    return extract_references_with_gemini(content, diagram.content_type)


@router.post("/extract-structured", response_model=StructuredExtraction, response_model_exclude_none=True)
async def extract_structured(
    diagram: UploadFile = File(...),
    child_diagrams: list[UploadFile] | None = File(None),
) -> StructuredExtraction:
    content = await diagram.read()
    return extract_structured_with_gemini(content, diagram.content_type, await read_child_drawings(child_diagrams))


@router.post("/extract-cost-breakdown", response_model=StructuredCostBreakdown, response_model_exclude_none=True)
async def extract_cost_breakdown(
    diagram: UploadFile = File(...),
    child_diagrams: list[UploadFile] | None = File(None),
    material_rate_per_kg: float | None = Form(RATE_PER_KG),
    laser_cutting_rate_per_meter: float = Form(RATE_PER_CUT_METER),
    press_machine_rate_per_hit: float = Form(RATE_PER_PRESS_MACHINE_HIT),
    bend_rate_per_bend: float = Form(RATE_PER_BEND_STROKE),
    welding_labor_per_meter: float = Form(LABOR_WELDING_PER_METER),
    painting_rate_per_m2: float = Form(RATE_PER_SQ_METER_PAINT),
    scrap_rate_per_kg: float = Form(28.0),
    tacking_fixed_setup_cost: float = Form(LABOR_TACKING_FIXED),
) -> StructuredCostBreakdown:
    content = await diagram.read()
    extraction = extract_structured_with_gemini(content, diagram.content_type, await read_child_drawings(child_diagrams))
    return calculate_structured_cost_breakdown(
        extraction,
        material_rate_per_kg=material_rate_per_kg,
        laser_cutting_rate_per_meter=laser_cutting_rate_per_meter,
        press_machine_rate_per_hit=press_machine_rate_per_hit,
        bend_rate_per_bend=bend_rate_per_bend,
        welding_labor_per_meter=welding_labor_per_meter,
        painting_rate_per_m2=painting_rate_per_m2,
        scrap_rate_per_kg=scrap_rate_per_kg,
        tacking_fixed_setup_cost=tacking_fixed_setup_cost,
    )
