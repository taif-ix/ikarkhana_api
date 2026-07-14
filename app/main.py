from __future__ import annotations

import io
import importlib.util
import json
import math
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator


app = FastAPI(title="Diagram Cost Estimator POC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


SS304_DENSITY_KG_PER_MM3 = 7.9e-6
CURRENCY_UNIT = "INR"
RATE_PER_KG = 255.00
RATE_PER_CUT_METER = 30.00
RATE_PER_BEND_STROKE = 5.00
RATE_PER_SQ_METER_PAINT = 120.00
RATE_PER_PRESS_MACHINE_HIT = 5.00
LABOR_WELDING_PER_METER = 400.00
LABOR_TACKING_FIXED = 1040.00
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_GEMINI_MODELS = {
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
}


def load_project_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("$env:"):
            line = line.removeprefix("$env:")

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_project_env()


class CalculationStep(BaseModel):
    section: str
    name: str
    formula: str
    substituted_values: str
    result: str


class LineItem(BaseModel):
    name: str
    quantity: int
    weight_kg: float
    material_cost: float
    process_cost: float
    total_cost: float
    formulas: dict[str, CalculationStep] = Field(default_factory=dict)


class ProcessBreakdown(BaseModel):
    cutting_cost: float
    bending_cost: float
    welding_cost: float
    press_machine_cost: float
    tacking_cost: float
    cutting_length_mm: float
    bend_count: int
    weld_length_mm: float
    press_machine_hits: int


class GeminiConfig(BaseModel):
    provider: Literal["gemini_api"]
    api_key_configured: bool
    project_configured: bool
    project: str | None
    location: str
    model: str
    google_genai_installed: bool
    pillow_installed: bool


class ExtractedDimensions(BaseModel):
    part_name: str = "Pillar Assembly"
    main_material_form: str | None = None
    main_profile_shape: str | None = None
    main_profile_is_hollow: bool | None = None
    main_profile_length_mm: float | None = None
    main_profile_outer_a_mm: float | None = None
    main_profile_outer_b_mm: float | None = None
    main_profile_diameter_mm: float | None = None
    main_profile_thickness_mm: float | None = None
    square_tube_length_mm: float | None = None
    square_tube_outer_mm: float | None = None
    square_tube_thickness_mm: float | None = None
    bottom_plate_l_mm: float | None = None
    bottom_plate_w_mm: float | None = None
    bottom_plate_t_mm: float | None = None
    top_plate_l_mm: float | None = None
    top_plate_w_mm: float | None = None
    top_plate_t_mm: float | None = None
    handle_od_mm: float | None = None
    handle_thickness_mm: float | None = None
    handle_length_mm: float | None = None
    screw_piece_dia_mm: float | None = None
    screw_piece_length_mm: float | None = None
    screw_piece_qty: int | None = None
    chair_angle_weight_per_m: float | None = None
    chair_angle_length_mm: float | None = None
    cutting_length_mm: float | None = None
    weld_length_mm: float | None = None
    bend_count: int | None = None
    confidence: float = 0
    notes: list[str] = []
    source: Literal["gemini_api"] = "gemini_api"

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]


class EstimateResponse(BaseModel):
    part_name: str
    likely_use: str
    uploaded_file: str
    file_size_kb: float
    total_weight_kg: float
    total_material_cost: float
    total_process_cost: float
    surface_treatment_cost: float
    total_estimated_cost: float
    assumptions: list[str]
    items: list[LineItem]
    process_breakdown: ProcessBreakdown
    calculation_steps: list[CalculationStep]


def round_money(value: float) -> float:
    return round(value, 2)


def kg(value: float) -> str:
    return f"{round(value, 3)} kg"


def money(value: float) -> str:
    return f"{CURRENCY_UNIT} {round_money(value)}"


def square_tube_weight(outer_mm: float, thickness_mm: float, length_mm: float) -> float:
    inner = max(outer_mm - (2 * thickness_mm), 0)
    area = (outer_mm * outer_mm) - (inner * inner)
    return area * length_mm * SS304_DENSITY_KG_PER_MM3


def round_tube_weight(od_mm: float, thickness_mm: float, length_mm: float) -> float:
    inner = max(od_mm - (2 * thickness_mm), 0)
    area = math.pi / 4 * ((od_mm * od_mm) - (inner * inner))
    return area * length_mm * SS304_DENSITY_KG_PER_MM3


def plate_weight(length_mm: float, width_mm: float, thickness_mm: float) -> float:
    return length_mm * width_mm * thickness_mm * SS304_DENSITY_KG_PER_MM3


def rod_weight(diameter_mm: float, length_mm: float) -> float:
    area = math.pi / 4 * diameter_mm * diameter_mm
    return area * length_mm * SS304_DENSITY_KG_PER_MM3


def tube_surface_area_m2(perimeter_mm: float, length_mm: float) -> float:
    return perimeter_mm * length_mm / 1_000_000


def plate_surface_area_m2(length_mm: float, width_mm: float, thickness_mm: float) -> float:
    return 2 * ((length_mm * width_mm) + (length_mm * thickness_mm) + (width_mm * thickness_mm)) / 1_000_000


def square_tube_steps(name: str, outer_mm: float, thickness_mm: float, length_mm: float, weight: float) -> list[CalculationStep]:
    inner = max(outer_mm - (2 * thickness_mm), 0)
    area = (outer_mm * outer_mm) - (inner * inner)
    volume = area * length_mm
    return [
        CalculationStep(
            section="Weight",
            name=f"{name} inner size",
            formula="Inner size = Outer size - (2 x wall thickness)",
            substituted_values=f"{outer_mm} - (2 x {thickness_mm})",
            result=f"{inner} mm",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} steel area",
            formula="Steel area = Outer area - Inner hollow area",
            substituted_values=f"({outer_mm} x {outer_mm}) - ({inner} x {inner})",
            result=f"{round(area, 3)} mm2",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} volume",
            formula="Volume = Steel area x length",
            substituted_values=f"{round(area, 3)} x {length_mm}",
            result=f"{round(volume, 3)} mm3",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} weight",
            formula="Weight = Volume x SS304 density",
            substituted_values=f"{round(volume, 3)} x {SS304_DENSITY_KG_PER_MM3} kg/mm3",
            result=kg(weight),
        ),
    ]


def plate_steps(name: str, length_mm: float, width_mm: float, thickness_mm: float, weight: float) -> list[CalculationStep]:
    volume = length_mm * width_mm * thickness_mm
    return [
        CalculationStep(
            section="Weight",
            name=f"{name} volume",
            formula="Volume = length x width x thickness",
            substituted_values=f"{length_mm} x {width_mm} x {thickness_mm}",
            result=f"{round(volume, 3)} mm3",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} weight",
            formula="Weight = Volume x SS304 density",
            substituted_values=f"{round(volume, 3)} x {SS304_DENSITY_KG_PER_MM3} kg/mm3",
            result=kg(weight),
        ),
    ]


def round_tube_steps(name: str, od_mm: float, thickness_mm: float, length_mm: float, weight: float) -> list[CalculationStep]:
    inner = max(od_mm - (2 * thickness_mm), 0)
    area = math.pi / 4 * ((od_mm * od_mm) - (inner * inner))
    volume = area * length_mm
    return [
        CalculationStep(
            section="Weight",
            name=f"{name} inside diameter",
            formula="Inside diameter = Outside diameter - (2 x wall thickness)",
            substituted_values=f"{od_mm} - (2 x {thickness_mm})",
            result=f"{inner} mm",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} steel area",
            formula="Steel area = pi / 4 x (OD2 - ID2)",
            substituted_values=f"pi / 4 x ({od_mm}2 - {inner}2)",
            result=f"{round(area, 3)} mm2",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} weight",
            formula="Weight = Steel area x length x SS304 density",
            substituted_values=f"{round(area, 3)} x {length_mm} x {SS304_DENSITY_KG_PER_MM3} kg/mm3",
            result=kg(weight),
        ),
    ]


def rod_steps(name: str, diameter_mm: float, length_mm: float, quantity: int, weight: float) -> list[CalculationStep]:
    area = math.pi / 4 * diameter_mm * diameter_mm
    volume_each = area * length_mm
    return [
        CalculationStep(
            section="Weight",
            name=f"{name} steel area",
            formula="Solid round area = pi / 4 x diameter2",
            substituted_values=f"pi / 4 x {diameter_mm}2",
            result=f"{round(area, 3)} mm2",
        ),
        CalculationStep(
            section="Weight",
            name=f"{name} total weight",
            formula="Weight = area x length x density x quantity",
            substituted_values=f"{round(area, 3)} x {length_mm} x {SS304_DENSITY_KG_PER_MM3} x {quantity}",
            result=kg(weight),
        ),
    ]


def find_step(steps: list[CalculationStep], name: str) -> CalculationStep:
    for step in steps:
        if step.name == name:
            return step
    return CalculationStep(
        section="Formula",
        name=name,
        formula="Formula not available",
        substituted_values="-",
        result="-",
    )


def package_installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except ModuleNotFoundError:
        return False


def extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return cleaned
    return cleaned[start : end + 1]


def repair_json_response(text: str) -> str:
    cleaned = extract_json_object(text)
    cleaned = cleaned.replace("\ufeff", "")
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    cleaned = re.sub(r'("[^"]*"\s*:\s*(?:"(?:\\.|[^"\\])*"|[-+]?\d+(?:\.\d+)?|true|false|null))\s*\n\s*"', r'\1,\n"', cleaned)
    cleaned = re.sub(r'(\])\s*\n\s*"', r'\1,\n"', cleaned)
    cleaned = re.sub(r'(\})\s*\n\s*"', r'\1,\n"', cleaned)
    return cleaned


def clean_json_response(text: str) -> dict:
    cleaned = extract_json_object(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(repair_json_response(text))


def image_bytes_for_gemini(content: bytes, content_type: str | None) -> tuple[bytes, str]:
    mime_type = content_type or "application/octet-stream"
    if mime_type in {"image/tiff", "image/tif"}:
        try:
            from PIL import Image
        except ImportError as exc:
            raise HTTPException(status_code=500, detail="Pillow is required to convert TIFF files.") from exc

        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue(), "image/png"

    return content, mime_type


def image_bytes_for_preview(content: bytes, content_type: str | None, filename: str | None) -> tuple[bytes, str]:
    mime_type = content_type or "application/octet-stream"
    filename = filename or ""
    is_tiff = mime_type in {"image/tiff", "image/tif"} or filename.lower().endswith((".tif", ".tiff"))
    if is_tiff:
        try:
            from PIL import Image
        except ImportError as exc:
            raise HTTPException(status_code=500, detail="Pillow is required to preview TIFF files.") from exc

        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue(), "image/png"

    return content, mime_type


def extract_dimensions_with_gemini(content: bytes, content_type: str | None) -> ExtractedDimensions:
    api_key = os.getenv("GEMINI_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

    if not api_key or api_key == "your-gemini-api-key":
        raise HTTPException(
            status_code=503,
            detail="Set GEMINI_API_KEY to call Gemini API for dimension extraction.",
        )

    if model not in ALLOWED_GEMINI_MODELS:
        raise HTTPException(
            status_code=503,
            detail=f"GEMINI_MODEL must be one of: {', '.join(sorted(ALLOWED_GEMINI_MODELS))}.",
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Install google-genai to enable Gemini API extraction.") from exc

    image_content, mime_type = image_bytes_for_gemini(content, content_type)
    client = genai.Client(api_key=api_key)

    prompt = """
You are extracting manufacturing costing inputs from an engineering drawing image.
Return only a compact valid JSON object. Do not use markdown. Do not add comments.
Every property must be separated by a comma. Use double quotes for all JSON keys.
The JSON object must match these keys:
part_name,
main_material_form, main_profile_shape, main_profile_is_hollow,
main_profile_length_mm, main_profile_outer_a_mm, main_profile_outer_b_mm,
main_profile_diameter_mm, main_profile_thickness_mm,
square_tube_length_mm, square_tube_outer_mm, square_tube_thickness_mm,
bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm,
top_plate_l_mm, top_plate_w_mm, top_plate_t_mm,
handle_od_mm, handle_thickness_mm, handle_length_mm,
screw_piece_dia_mm, screw_piece_length_mm, screw_piece_qty,
chair_angle_weight_per_m, chair_angle_length_mm,
cutting_length_mm, weld_length_mm, bend_count, confidence, notes.

Use numbers in millimeters. If a value is not visible, use null and explain in notes.
Raw material rule:
- Identify whether each part is made from rod/bar/profile or blank sheet/plate.
- Rod/bar/profile supplier standard length is 6000 mm.
- Rod/profile can be solid or hollow.
- Rod/profile cross section can be circular, rectangular, or square.
- For main_material_form use one of: rod_profile, blank_sheet, unknown.
- For main_profile_shape use one of: circular, square, rectangular, unknown.
- main_profile_is_hollow should be true for tube/pipe/SHS/RHS and false for solid rod/bar.
- For circular rod/profile, fill main_profile_diameter_mm.
- For square rod/profile, fill main_profile_outer_a_mm and main_profile_outer_b_mm with the same value.
- For rectangular rod/profile, fill main_profile_outer_a_mm and main_profile_outer_b_mm.
- For hollow circular sections, use outside diameter and wall thickness.
- For hollow rectangular/square sections, use outer A, outer B, and wall thickness.
- For blank sheet/plate, use length x breadth x thickness.
- Rectangle/square sheet supplier standard size is 2500 x 1250 mm.
Cutting dimension rule:
- Rectangular/square blank cutting length = 2 x (L + B).
- Circular cutting length = pi x diameter.
- Total cutting length should include profile cut lengths plus visible blank/perimeter cuts.
For cutting_length_mm, estimate from visible tube/profile lengths plus plate perimeters and circular/rectangular cuts.
For weld_length_mm, estimate from visible weld symbols and joint perimeters.
Do not invent hidden detail drawing dimensions.
"""

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=image_content, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        message = str(exc)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            raise HTTPException(
                status_code=401,
                detail="Gemini API key is invalid. Create/copy a valid key from Google AI Studio and update GEMINI_API_KEY.",
            ) from exc
        if "RESOURCE_EXHAUSTED" in message or "Quota exceeded" in message:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Gemini quota is exhausted for model {model}. For a no-billing POC, try "
                    "GEMINI_MODEL=gemini-3.5-flash or gemini-flash-lite-latest, wait for the retry window, or enable billing/quota."
                ),
            ) from exc
        if "NOT_FOUND" in message or "no longer available" in message:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Gemini model {model} is not available for this API key. Try "
                    "GEMINI_MODEL=gemini-3.5-flash or gemini-flash-lite-latest."
                ),
            ) from exc
        if "BILLING_DISABLED" in message or "requires billing to be enabled" in message:
            raise HTTPException(
                status_code=402,
                detail=(
                    "This Gemini API request requires billing for the API key's Google project. "
                    "Use a key with available free-tier quota or enable billing."
                ),
            ) from exc
        if "SERVICE_DISABLED" in message:
            raise HTTPException(
                status_code=503,
                detail="Gemini API is disabled for this API key's project. Enable the Generative Language API, then retry.",
            ) from exc
        if "PERMISSION_DENIED" in message:
            raise HTTPException(
                status_code=403,
                detail=f"Gemini API permission denied: {message}",
            ) from exc
        raise HTTPException(status_code=502, detail=f"Gemini API extraction failed: {message}") from exc

    if not response.text:
        raise HTTPException(status_code=502, detail="Gemini 2.5 Pro returned an empty extraction response.")

    try:
        extracted = ExtractedDimensions.model_validate(clean_json_response(response.text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned a response that could not be parsed as extraction JSON: {exc}",
        ) from exc

    extracted.source = "gemini_api"
    return extracted


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def api_root() -> dict[str, object]:
    return {
        "service": "cost_estimator_api",
        "status": "ok",
        "frontend_repo": "https://github.com/taif-ix/ikarkhana_web",
        "docs": "/docs",
        "endpoints": [
            "/health",
            "/gemini-config",
            "/diagram-preview",
            "/extract-dimensions",
            "/estimate",
        ],
    }


@app.get("/gemini-config", response_model=GeminiConfig)
def gemini_config() -> GeminiConfig:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    api_key = os.getenv("GEMINI_API_KEY")
    return GeminiConfig(
        provider="gemini_api",
        api_key_configured=bool(api_key and api_key != "your-gemini-api-key"),
        project_configured=bool(project and project != "your-gcp-project-id"),
        project=project,
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1"),
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        google_genai_installed=package_installed("google.genai"),
        pillow_installed=package_installed("PIL"),
    )


@app.get("/vertex-config", response_model=GeminiConfig)
def vertex_config() -> GeminiConfig:
    return gemini_config()


@app.post("/extract-dimensions", response_model=ExtractedDimensions)
async def extract_dimensions(diagram: UploadFile = File(...)) -> ExtractedDimensions:
    content = await diagram.read()
    return extract_dimensions_with_gemini(content, diagram.content_type)


@app.post("/diagram-preview")
async def diagram_preview(diagram: UploadFile = File(...)) -> StreamingResponse:
    content = await diagram.read()
    image_content, mime_type = image_bytes_for_preview(content, diagram.content_type, diagram.filename)
    return StreamingResponse(io.BytesIO(image_content), media_type=mime_type)


@app.post("/estimate", response_model=EstimateResponse)
async def estimate(
    diagram: UploadFile = File(...),
    part_name: str = Form("Pillar Assembly"),
    material_rate_per_kg: float = Form(RATE_PER_KG),
    cutting_rate_per_meter: float = Form(RATE_PER_CUT_METER),
    welding_labor_per_meter: float = Form(LABOR_WELDING_PER_METER),
    surface_rate_per_m2: float = Form(RATE_PER_SQ_METER_PAINT),
    surface_type: Literal["satin_passivated", "painted", "none"] = Form("satin_passivated"),
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
    weld_length_mm: float = Form(850.0),
    bend_count: int = Form(2),
    bend_rate_per_stroke: float = Form(RATE_PER_BEND_STROKE),
    press_machine_hits: int = Form(0),
    press_machine_rate_per_hit: float = Form(RATE_PER_PRESS_MACHINE_HIT),
    include_tacking_labor: bool = Form(False),
    tacking_labor_fixed: float = Form(LABOR_TACKING_FIXED),
) -> EstimateResponse:
    content = await diagram.read()
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

    tube_weight = square_tube_weight(square_tube_outer_mm, square_tube_thickness_mm, square_tube_length_mm)
    bottom_weight = plate_weight(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm)
    top_weight = plate_weight(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm)
    handle_weight = round_tube_weight(handle_od_mm, handle_thickness_mm, handle_length_mm)
    screw_weight_each = rod_weight(screw_piece_dia_mm, screw_piece_length_mm)
    screw_weight = screw_weight_each * screw_piece_qty
    chair_angle_weight_kg = chair_angle_weight_per_m * (chair_angle_length_mm / 1000)

    calculation_steps: list[CalculationStep] = []
    calculation_steps.extend(
        square_tube_steps(
            "Square tube",
            square_tube_outer_mm,
            square_tube_thickness_mm,
            square_tube_length_mm,
            tube_weight,
        )
    )
    calculation_steps.extend(plate_steps("Bottom plate", bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, bottom_weight))
    calculation_steps.extend(plate_steps("Top plate", top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, top_weight))
    calculation_steps.extend(round_tube_steps("Handle tube", handle_od_mm, handle_thickness_mm, handle_length_mm, handle_weight))
    calculation_steps.extend(rod_steps("Screwing pieces", screw_piece_dia_mm, screw_piece_length_mm, screw_piece_qty, screw_weight))
    calculation_steps.append(
        CalculationStep(
            section="Weight",
            name="Chair angle / bracket weight",
            formula="Weight = section weight per meter x length in meter",
            substituted_values=f"{chair_angle_weight_per_m} kg/m x ({chair_angle_length_mm} / 1000)",
            result=kg(chair_angle_weight_kg),
        )
    )

    items = [
        ("Square tube 45x45x4", 1, tube_weight, "Square tube weight"),
        ("Bottom plate", 1, bottom_weight, "Bottom plate weight"),
        ("Top plate", 1, top_weight, "Top plate weight"),
        ("Chair angle / bracket", 1, chair_angle_weight_kg, "Chair angle / bracket weight"),
        ("Handle tube", 1, handle_weight, "Handle tube weight"),
        ("Screwing piece", screw_piece_qty, screw_weight, "Screwing pieces total weight"),
    ]

    cutting_length_m = cutting_length_mm / 1000
    weld_length_m = weld_length_mm / 1000
    cutting_cost = cutting_length_m * cutting_rate_per_meter
    bending_cost = bend_count * bend_rate_per_stroke
    welding_cost = weld_length_m * welding_labor_per_meter
    press_machine_cost = press_machine_hits * press_machine_rate_per_hit
    tacking_cost = tacking_labor_fixed if include_tacking_labor else 0.0
    total_process_cost = cutting_cost + bending_cost + welding_cost + press_machine_cost + tacking_cost

    calculation_steps.extend(
        [
            CalculationStep(
                section="Process",
                name="Cutting cost",
                formula="Cutting cost = Total cutting length in meters x cutting rate per meter",
                substituted_values=f"({cutting_length_mm} / 1000) m x {CURRENCY_UNIT} {cutting_rate_per_meter}/m",
                result=money(cutting_cost),
            ),
            CalculationStep(
                section="Process",
                name="Bending cost",
                formula="Bending cost = Number of bend strokes x rate per bend stroke",
                substituted_values=f"{bend_count} x {CURRENCY_UNIT} {bend_rate_per_stroke}",
                result=money(bending_cost),
            ),
            CalculationStep(
                section="Process",
                name="Welding cost",
                formula="Welding cost = Total weld length in meters x welding labor per meter",
                substituted_values=f"({weld_length_mm} / 1000) m x {CURRENCY_UNIT} {welding_labor_per_meter}/m",
                result=money(welding_cost),
            ),
            CalculationStep(
                section="Process",
                name="Press machine cost",
                formula="Press machine cost = Number of machine hits x rate per hit",
                substituted_values=f"{press_machine_hits} x {CURRENCY_UNIT} {press_machine_rate_per_hit}",
                result=money(press_machine_cost),
            ),
            CalculationStep(
                section="Process",
                name="Tacking labor",
                formula="Tacking labor = fixed tacking labor when included",
                substituted_values=f"{'included' if include_tacking_labor else 'not included'}; fixed = {CURRENCY_UNIT} {tacking_labor_fixed}",
                result=money(tacking_cost),
            ),
        ]
    )

    surface_area = (
        tube_surface_area_m2(4 * square_tube_outer_mm, square_tube_length_mm)
        + plate_surface_area_m2(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm)
        + plate_surface_area_m2(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm)
        + tube_surface_area_m2(math.pi * handle_od_mm, handle_length_mm)
    )
    surface_cost = 0.0 if surface_type == "none" else surface_area * surface_rate_per_m2
    calculation_steps.extend(
        [
            CalculationStep(
                section="Surface",
                name="Surface area",
                formula="Surface area = tube outside perimeter x length + plate exposed areas + handle outside perimeter x length",
                substituted_values=(
                    f"(4 x {square_tube_outer_mm} x {square_tube_length_mm}) + "
                    f"plate areas + (pi x {handle_od_mm} x {handle_length_mm})"
                ),
                result=f"{round(surface_area, 4)} m2",
            ),
            CalculationStep(
                section="Surface",
                name="Surface treatment cost",
                formula="Surface treatment cost = Surface area x surface treatment rate",
                substituted_values=f"{round(surface_area, 4)} m2 x {CURRENCY_UNIT} {surface_rate_per_m2}/m2",
                result=money(surface_cost),
            ),
        ]
    )

    line_items: list[LineItem] = []
    total_weight = 0.0
    total_material_cost = 0.0
    for name, quantity, weight, weight_step_name in items:
        material_cost = weight * material_rate_per_kg
        total_weight += weight
        total_material_cost += material_cost
        weight_formula = find_step(calculation_steps, weight_step_name)
        material_formula = CalculationStep(
            section="Cost",
            name=f"{name} material cost",
            formula="Material cost = item weight x material rate",
            substituted_values=f"{round(weight, 3)} kg x {CURRENCY_UNIT} {material_rate_per_kg}/kg",
            result=money(material_cost),
        )
        line_items.append(
            LineItem(
                name=name,
                quantity=quantity,
                weight_kg=round(weight, 3),
                material_cost=round_money(material_cost),
                process_cost=0,
                total_cost=round_money(material_cost),
                formulas={
                    "weight": weight_formula,
                    "material": material_formula,
                    "total": material_formula,
                },
            )
        )

    total = total_material_cost + total_process_cost + surface_cost
    calculation_steps.extend(
        [
            CalculationStep(
                section="Cost",
                name="Material cost",
                formula="Material cost = Total calculated weight x material rate",
                substituted_values=f"{round(total_weight, 3)} kg x {CURRENCY_UNIT} {material_rate_per_kg}/kg",
                result=money(total_material_cost),
            ),
            CalculationStep(
                section="Cost",
                name="Total estimated cost",
                formula="Total cost = Material cost + process cost + surface treatment cost",
                substituted_values=f"{CURRENCY_UNIT} {round_money(total_material_cost)} + {CURRENCY_UNIT} {round_money(total_process_cost)} + {CURRENCY_UNIT} {round_money(surface_cost)}",
                result=money(total),
            ),
        ]
    )

    return EstimateResponse(
        part_name=part_name,
        likely_use="Vertical pillar/post used in a rail coach partition frame or similar structural partition assembly.",
        uploaded_file=diagram.filename or "uploaded diagram",
        file_size_kb=round(len(content) / 1024, 2),
        total_weight_kg=round(total_weight, 3),
        total_material_cost=round_money(total_material_cost),
        total_process_cost=round_money(total_process_cost),
        surface_treatment_cost=round_money(surface_cost),
        total_estimated_cost=round_money(total),
        assumptions=[
            f"Dimension extraction is handled by Gemini API with {model}; this costing step uses the submitted field values.",
            "Default values are based on LS10255 Pillar Assembly notes shared with the request.",
            "Standard raw material nesting, scrap recovery, tax, packaging, transport, and supplier MOQ are not included.",
            "Chair angle weight uses kg/m x length because the detailed LS10269 geometry is not present in the upload.",
            f"Process cost includes cutting ({cutting_length_mm} mm), bending ({bend_count} strokes), welding ({weld_length_mm} mm), press hits ({press_machine_hits}), and optional tacking labor.",
        ],
        items=line_items,
        process_breakdown=ProcessBreakdown(
            cutting_cost=round_money(cutting_cost),
            bending_cost=round_money(bending_cost),
            welding_cost=round_money(welding_cost),
            press_machine_cost=round_money(press_machine_cost),
            tacking_cost=round_money(tacking_cost),
            cutting_length_mm=cutting_length_mm,
            bend_count=bend_count,
            weld_length_mm=weld_length_mm,
            press_machine_hits=press_machine_hits,
        ),
        calculation_steps=calculation_steps,
    )
