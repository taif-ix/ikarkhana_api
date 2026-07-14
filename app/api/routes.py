from __future__ import annotations

import io
import os

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import (
    LABOR_TACKING_FIXED,
    RATE_PER_BEND_STROKE,
    RATE_PER_CUT_METER,
    RATE_PER_KG,
    RATE_PER_PRESS_MACHINE_HIT,
    RATE_PER_SQ_METER_PAINT,
    LABOR_WELDING_PER_METER,
)
from app.models.schemas import EstimateResponse, ExtractedDimensions, GeminiConfig
from app.services.estimator import calculate_estimate
from app.services.vision import extract_dimensions_with_gemini, image_bytes_for_preview, package_installed


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
        "endpoints": ["/health", "/gemini-config", "/diagram-preview", "/extract-dimensions", "/estimate"],
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


@router.post("/extract-dimensions", response_model=ExtractedDimensions)
async def extract_dimensions(diagram: UploadFile = File(...)) -> ExtractedDimensions:
    content = await diagram.read()
    return extract_dimensions_with_gemini(content, diagram.content_type)


@router.post("/diagram-preview")
async def diagram_preview(diagram: UploadFile = File(...)) -> StreamingResponse:
    content = await diagram.read()
    image_content, mime_type = image_bytes_for_preview(content, diagram.content_type, diagram.filename)
    return StreamingResponse(io.BytesIO(image_content), media_type=mime_type)


@router.post("/estimate", response_model=EstimateResponse)
async def estimate(
    diagram: UploadFile = File(...),
    part_name: str = Form("Pillar Assembly"),
    raw_material_type: str = Form("ss"),
    raw_material_code: str | None = Form(None),
    component_materials_json: str | None = Form(None),
    material_rate_per_kg: float | None = Form(RATE_PER_KG),
    cutting_rate_per_meter: float = Form(RATE_PER_CUT_METER),
    welding_labor_per_meter: float = Form(LABOR_WELDING_PER_METER),
    surface_rate_per_m2: float = Form(RATE_PER_SQ_METER_PAINT),
    surface_type: str = Form("satin_passivated"),
    square_tube_length_mm: float = Form(2581.0),
    square_tube_outer_mm: float = Form(45.0),
    square_tube_thickness_mm: float = Form(4.0),
    bottom_plate_l_mm: float = Form(150.0),
    bottom_plate_w_mm: float = Form(100.0),
    bottom_plate_t_mm: float = Form(5.0),
    top_plate_l_mm: float = Form(125.0),
    top_plate_w_mm: float = Form(125.0),
    top_plate_t_mm: float = Form(5.0),
    handle_od_mm: float = Form(19.0),
    handle_thickness_mm: float = Form(2.0),
    handle_length_mm: float = Form(288.0),
    screw_piece_dia_mm: float = Form(20.0),
    screw_piece_length_mm: float = Form(45.0),
    screw_piece_qty: int = Form(4),
    chair_angle_weight_per_m: float = Form(2.42),
    chair_angle_length_mm: float = Form(620.0),
    cutting_length_mm: float = Form(3869.0),
    cutting_surface_count: int = Form(0),
    weld_length_mm: float = Form(850.0),
    bend_count: int = Form(2),
    bend_rate_per_stroke: float = Form(RATE_PER_BEND_STROKE),
    press_machine_hits: int = Form(0),
    press_machine_rate_per_hit: float = Form(RATE_PER_PRESS_MACHINE_HIT),
    include_tacking_labor: bool = Form(False),
    tacking_labor_fixed: float = Form(LABOR_TACKING_FIXED),
) -> EstimateResponse:
    content = await diagram.read()
    return calculate_estimate(
        content=content,
        filename=diagram.filename,
        part_name=part_name,
        raw_material_type=raw_material_type,
        raw_material_code=raw_material_code,
        component_materials_json=component_materials_json,
        material_rate_per_kg=material_rate_per_kg,
        cutting_rate_per_meter=cutting_rate_per_meter,
        welding_labor_per_meter=welding_labor_per_meter,
        surface_rate_per_m2=surface_rate_per_m2,
        surface_type=surface_type,
        square_tube_length_mm=square_tube_length_mm,
        square_tube_outer_mm=square_tube_outer_mm,
        square_tube_thickness_mm=square_tube_thickness_mm,
        bottom_plate_l_mm=bottom_plate_l_mm,
        bottom_plate_w_mm=bottom_plate_w_mm,
        bottom_plate_t_mm=bottom_plate_t_mm,
        top_plate_l_mm=top_plate_l_mm,
        top_plate_w_mm=top_plate_w_mm,
        top_plate_t_mm=top_plate_t_mm,
        handle_od_mm=handle_od_mm,
        handle_thickness_mm=handle_thickness_mm,
        handle_length_mm=handle_length_mm,
        screw_piece_dia_mm=screw_piece_dia_mm,
        screw_piece_length_mm=screw_piece_length_mm,
        screw_piece_qty=screw_piece_qty,
        chair_angle_weight_per_m=chair_angle_weight_per_m,
        chair_angle_length_mm=chair_angle_length_mm,
        cutting_length_mm=cutting_length_mm,
        cutting_surface_count=cutting_surface_count,
        weld_length_mm=weld_length_mm,
        bend_count=bend_count,
        bend_rate_per_stroke=bend_rate_per_stroke,
        press_machine_hits=press_machine_hits,
        press_machine_rate_per_hit=press_machine_rate_per_hit,
        include_tacking_labor=include_tacking_labor,
        tacking_labor_fixed=tacking_labor_fixed,
    )
