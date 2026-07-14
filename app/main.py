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
RATE_MS_PER_KG = 60.00
RATE_SS_PER_KG = 240.00
RATE_ALUMINIUM_PER_KG = 200.00
RATE_COPPER_PER_KG = 900.00
RATE_PER_KG = RATE_SS_PER_KG
RATE_PER_CUT_METER = 200.00
RATE_PER_BEND_STROKE = 2.00
RATE_PER_SQ_METER_PAINT = 120.00
RATE_PER_PRESS_MACHINE_HIT = 5.00
LABOR_WELDING_PER_METER = 22.00
LABOR_TACKING_FIXED = 1040.00
SCRAP_RATE_PER_KG = 28.00
ROD_STOCK_LENGTH_MM = 6000.00
SHEET_STOCK_LENGTH_MM = 2500.00
SHEET_STOCK_WIDTH_MM = 1250.00
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_GEMINI_MODELS = {
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
}

MATERIALS = {
    "ms": {
        "label": "Mild Steel",
        "density": 7.85e-6,
        "default_rate": RATE_MS_PER_KG,
        "codes": ["ms", "mild steel", "is2062", "e250", "e350"],
    },
    "ss": {
        "label": "Stainless Steel",
        "density": SS304_DENSITY_KG_PER_MM3,
        "default_rate": RATE_SS_PER_KG,
        "codes": ["ss", "stainless", "304", "316", "c-k201", "k201", "ck201"],
    },
    "aluminium": {
        "label": "Aluminium",
        "density": 2.70e-6,
        "default_rate": RATE_ALUMINIUM_PER_KG,
        "codes": ["al", "alu", "aluminium", "aluminum", "6061", "6082"],
    },
    "copper": {
        "label": "Copper",
        "density": 8.96e-6,
        "default_rate": RATE_COPPER_PER_KG,
        "codes": ["cu", "copper", "c11000", "etp"],
    },
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
    material_type: str = "ss"
    material_label: str = "Stainless Steel"
    material_rate_per_kg: float = RATE_SS_PER_KG
    stock_form: str | None = None
    stock_size: str | None = None
    parts_per_stock: int | None = None
    stock_weight_kg: float | None = None
    gross_stock_cost: float | None = None
    scrap_weight_kg: float | None = None
    scrap_value: float | None = None
    net_stock_cost_per_part: float | None = None
    nesting_approach: str | None = None
    formulas: dict[str, CalculationStep] = Field(default_factory=dict)


class ProcessBreakdown(BaseModel):
    cutting_cost: float
    bending_cost: float
    welding_cost: float
    press_machine_cost: float
    painting_cost: float = 0
    tacking_cost: float
    cutting_length_mm: float
    cutting_surface_count: int = 0
    bend_count: int
    weld_length_mm: float
    press_machine_hits: int


class MaterialSummary(BaseModel):
    material_type: str
    material_label: str
    material_code: str | None
    density_kg_per_mm3: float
    rate_per_kg: float
    default_rate_per_kg: float
    source: str


class StockSummary(BaseModel):
    rod_stock_length_mm: float = ROD_STOCK_LENGTH_MM
    sheet_stock_length_mm: float = SHEET_STOCK_LENGTH_MM
    sheet_stock_width_mm: float = SHEET_STOCK_WIDTH_MM
    scrap_rate_per_kg: float = SCRAP_RATE_PER_KG
    total_scrap_weight_kg: float
    total_scrap_value: float
    approach: str


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
    raw_material_type: str | None = None
    raw_material_code: str | None = None
    component_materials: list[dict[str, str | float | int | None]] = Field(default_factory=list)
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
    cutting_surface_count: int | None = None
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
    material_summary: MaterialSummary
    stock_summary: StockSummary
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


def normalize_material_type(material_type: str | None, material_code: str | None = None) -> str:
    haystack = f"{material_type or ''} {material_code or ''}".lower().replace("_", " ").replace("-", "")
    for key, details in MATERIALS.items():
        for code in details["codes"]:
            if code.replace("-", "").lower() in haystack:
                return key
    return "ss"


def material_details(material_type: str | None, material_code: str | None = None) -> dict:
    return MATERIALS[normalize_material_type(material_type, material_code)]


def material_density(material_type: str | None, material_code: str | None = None) -> float:
    return float(material_details(material_type, material_code)["density"])


def default_material_rate(material_type: str | None, material_code: str | None = None) -> float:
    return float(material_details(material_type, material_code)["default_rate"])


def section_area_mm2(
    shape: str,
    is_hollow: bool,
    outer_a_mm: float,
    outer_b_mm: float | None,
    diameter_mm: float | None,
    thickness_mm: float,
) -> float:
    normalized = (shape or "square").lower()
    if normalized == "circular":
        od = diameter_mm or outer_a_mm
        if is_hollow:
            inner = max(od - (2 * thickness_mm), 0)
            return math.pi / 4 * ((od * od) - (inner * inner))
        return math.pi / 4 * od * od

    b = outer_b_mm or outer_a_mm
    if is_hollow:
        inner_a = max(outer_a_mm - (2 * thickness_mm), 0)
        inner_b = max(b - (2 * thickness_mm), 0)
        return (outer_a_mm * b) - (inner_a * inner_b)
    return outer_a_mm * b


def profile_weight(
    shape: str,
    is_hollow: bool,
    outer_a_mm: float,
    outer_b_mm: float | None,
    diameter_mm: float | None,
    thickness_mm: float,
    length_mm: float,
    density_kg_per_mm3: float,
) -> float:
    return section_area_mm2(shape, is_hollow, outer_a_mm, outer_b_mm, diameter_mm, thickness_mm) * length_mm * density_kg_per_mm3


def square_tube_weight(outer_mm: float, thickness_mm: float, length_mm: float, density_kg_per_mm3: float = SS304_DENSITY_KG_PER_MM3) -> float:
    inner = max(outer_mm - (2 * thickness_mm), 0)
    area = (outer_mm * outer_mm) - (inner * inner)
    return area * length_mm * density_kg_per_mm3


def round_tube_weight(od_mm: float, thickness_mm: float, length_mm: float, density_kg_per_mm3: float = SS304_DENSITY_KG_PER_MM3) -> float:
    inner = max(od_mm - (2 * thickness_mm), 0)
    area = math.pi / 4 * ((od_mm * od_mm) - (inner * inner))
    return area * length_mm * density_kg_per_mm3


def plate_weight(length_mm: float, width_mm: float, thickness_mm: float, density_kg_per_mm3: float = SS304_DENSITY_KG_PER_MM3) -> float:
    return length_mm * width_mm * thickness_mm * density_kg_per_mm3


def rod_weight(diameter_mm: float, length_mm: float, density_kg_per_mm3: float = SS304_DENSITY_KG_PER_MM3) -> float:
    area = math.pi / 4 * diameter_mm * diameter_mm
    return area * length_mm * density_kg_per_mm3


def rod_stock_summary(piece_weight_kg: float, piece_length_mm: float, quantity: int) -> dict[str, float | int | str]:
    pieces_per_stock = max(int(ROD_STOCK_LENGTH_MM // piece_length_mm), 1) if piece_length_mm > 0 else 1
    stock_count = math.ceil(quantity / pieces_per_stock)
    used_per_full_stock_mm = min(pieces_per_stock * piece_length_mm, ROD_STOCK_LENGTH_MM)
    leftover_per_full_stock_mm = max(ROD_STOCK_LENGTH_MM - used_per_full_stock_mm, 0)
    stock_weight = piece_weight_kg * (ROD_STOCK_LENGTH_MM / piece_length_mm) if piece_length_mm > 0 else piece_weight_kg
    scrap_weight = stock_weight * (leftover_per_full_stock_mm / ROD_STOCK_LENGTH_MM) * stock_count
    gross_stock_cost = stock_weight * stock_count
    return {
        "parts_per_stock": pieces_per_stock,
        "stock_count": stock_count,
        "stock_weight_kg": stock_weight,
        "gross_stock_weight_kg": gross_stock_cost,
        "scrap_weight_kg": scrap_weight,
        "leftover_per_stock_mm": leftover_per_full_stock_mm,
        "approach": f"Linear 6000 mm bar nesting: floor(6000 / {piece_length_mm}) = {pieces_per_stock} pieces, leftover {round(leftover_per_full_stock_mm, 2)} mm per full stock.",
    }


def sheet_nesting_summary(length_mm: float, width_mm: float, thickness_mm: float, density_kg_per_mm3: float, quantity: int) -> dict[str, float | int | str]:
    normal_cols = int(SHEET_STOCK_LENGTH_MM // length_mm) if length_mm > 0 else 0
    normal_rows = int(SHEET_STOCK_WIDTH_MM // width_mm) if width_mm > 0 else 0
    normal_count = normal_cols * normal_rows
    rotated_cols = int(SHEET_STOCK_LENGTH_MM // width_mm) if width_mm > 0 else 0
    rotated_rows = int(SHEET_STOCK_WIDTH_MM // length_mm) if length_mm > 0 else 0
    rotated_count = rotated_cols * rotated_rows
    if rotated_count > normal_count:
        parts_per_sheet = rotated_count
        approach = f"Rotated grid nesting on 2500 x 1250 sheet: floor(2500/{width_mm}) x floor(1250/{length_mm}) = {parts_per_sheet} parts."
    else:
        parts_per_sheet = normal_count
        approach = f"Straight grid nesting on 2500 x 1250 sheet: floor(2500/{length_mm}) x floor(1250/{width_mm}) = {parts_per_sheet} parts."
    parts_per_sheet = max(parts_per_sheet, 1)
    sheet_count = math.ceil(quantity / parts_per_sheet)
    sheet_weight = plate_weight(SHEET_STOCK_LENGTH_MM, SHEET_STOCK_WIDTH_MM, thickness_mm, density_kg_per_mm3)
    part_weight = plate_weight(length_mm, width_mm, thickness_mm, density_kg_per_mm3)
    used_weight = part_weight * min(quantity, parts_per_sheet * sheet_count)
    scrap_weight = max((sheet_weight * sheet_count) - used_weight, 0)
    return {
        "parts_per_stock": parts_per_sheet,
        "stock_count": sheet_count,
        "stock_weight_kg": sheet_weight,
        "scrap_weight_kg": scrap_weight,
        "approach": approach + " This is a simple rectangular nesting estimate; true CNC nesting may improve yield with mixed parts.",
    }


def tube_surface_area_m2(perimeter_mm: float, length_mm: float) -> float:
    return perimeter_mm * length_mm / 1_000_000


def plate_surface_area_m2(length_mm: float, width_mm: float, thickness_mm: float) -> float:
    return 2 * ((length_mm * width_mm) + (length_mm * thickness_mm) + (width_mm * thickness_mm)) / 1_000_000


def square_tube_steps(name: str, outer_mm: float, thickness_mm: float, length_mm: float, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
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
            formula=f"Weight = Volume x {material_label} density",
            substituted_values=f"{round(volume, 3)} x {density_kg_per_mm3} kg/mm3",
            result=kg(weight),
        ),
    ]


def plate_steps(name: str, length_mm: float, width_mm: float, thickness_mm: float, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
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
            formula=f"Weight = Volume x {material_label} density",
            substituted_values=f"{round(volume, 3)} x {density_kg_per_mm3} kg/mm3",
            result=kg(weight),
        ),
    ]


def round_tube_steps(name: str, od_mm: float, thickness_mm: float, length_mm: float, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
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
            formula=f"Weight = Steel area x length x {material_label} density",
            substituted_values=f"{round(area, 3)} x {length_mm} x {density_kg_per_mm3} kg/mm3",
            result=kg(weight),
        ),
    ]


def rod_steps(name: str, diameter_mm: float, length_mm: float, quantity: int, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
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
            substituted_values=f"{round(area, 3)} x {length_mm} x {density_kg_per_mm3} x {quantity} ({material_label})",
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
part_name, raw_material_type, raw_material_code, component_materials,
main_material_form, main_profile_shape, main_profile_is_hollow,
main_profile_length_mm, main_profile_outer_a_mm, main_profile_outer_b_mm,
main_profile_diameter_mm, main_profile_thickness_mm,
square_tube_length_mm, square_tube_outer_mm, square_tube_thickness_mm,
bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm,
top_plate_l_mm, top_plate_w_mm, top_plate_t_mm,
handle_od_mm, handle_thickness_mm, handle_length_mm,
screw_piece_dia_mm, screw_piece_length_mm, screw_piece_qty,
chair_angle_weight_per_m, chair_angle_length_mm,
cutting_length_mm, cutting_surface_count, weld_length_mm, bend_count, confidence, notes.

Use numbers in millimeters. If a value is not visible, use null and explain in notes.
Material rule:
- Detect the raw material from title block, BOM, grade/specification, or item notes.
- raw_material_type must be one of: ms, ss, aluminium, copper, unknown.
- raw_material_code should preserve the visible material code, for example C-K201.
- C-K201/K201/304/316/stainless means stainless steel unless a note says otherwise.
- MS/IS2062/E250/E350 means mild steel.
- AL/6061/6082 means aluminium.
- CU/copper/C11000 means copper.
- component_materials can list visible item-level material differences as objects with item, material_type, material_code, and note.
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
- cutting_surface_count is the number of separate cut surfaces/features. For example four holes on faces A/B/C/D means 4 surfaces.
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
    raw_material_type: str = Form("ss"),
    raw_material_code: str | None = Form(None),
    component_materials_json: str | None = Form(None),
    material_rate_per_kg: float | None = Form(None),
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
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    material_type = normalize_material_type(raw_material_type, raw_material_code)
    details = MATERIALS[material_type]
    material_label = str(details["label"])
    density = float(details["density"])
    default_rate = float(details["default_rate"])
    active_material_rate = material_rate_per_kg if material_rate_per_kg and material_rate_per_kg > 0 else default_rate
    component_materials: list[dict] = []
    if component_materials_json:
        try:
            parsed_component_materials = json.loads(component_materials_json)
            if isinstance(parsed_component_materials, list):
                component_materials = [item for item in parsed_component_materials if isinstance(item, dict)]
        except json.JSONDecodeError:
            component_materials = []

    tube_weight = square_tube_weight(square_tube_outer_mm, square_tube_thickness_mm, square_tube_length_mm, density)
    bottom_weight = plate_weight(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, density)
    top_weight = plate_weight(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, density)
    handle_weight = round_tube_weight(handle_od_mm, handle_thickness_mm, handle_length_mm, density)
    screw_weight_each = rod_weight(screw_piece_dia_mm, screw_piece_length_mm, density)
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
            density,
            material_label,
        )
    )
    calculation_steps.extend(plate_steps("Bottom plate", bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, bottom_weight, density, material_label))
    calculation_steps.extend(plate_steps("Top plate", top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, top_weight, density, material_label))
    calculation_steps.extend(round_tube_steps("Handle tube", handle_od_mm, handle_thickness_mm, handle_length_mm, handle_weight, density, material_label))
    calculation_steps.extend(rod_steps("Screwing pieces", screw_piece_dia_mm, screw_piece_length_mm, screw_piece_qty, screw_weight, density, material_label))
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
                formula="Laser cutting cost = Total cutting length in meters x laser cut rate per meter",
                substituted_values=f"({cutting_length_mm} / 1000) m x {CURRENCY_UNIT} {cutting_rate_per_meter}/m across {cutting_surface_count} cut surfaces",
                result=money(cutting_cost),
            ),
            CalculationStep(
                section="Process",
                name="Bending cost",
                formula="Bending cost = Number of bends x rate per bend",
                substituted_values=f"{bend_count} x {CURRENCY_UNIT} {bend_rate_per_stroke}",
                result=money(bending_cost),
            ),
            CalculationStep(
                section="Process",
                name="Welding cost",
                formula="Welding cost = Total weld length in meters x welding rate per meter",
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
    total_scrap_weight = 0.0
    total_scrap_value = 0.0
    for name, quantity, weight, weight_step_name in items:
        lower_name = name.lower()
        item_material_type = material_type
        item_material_code = raw_material_code
        for component_material in component_materials:
            item_name = str(component_material.get("item") or "").lower()
            if item_name and (item_name in name.lower() or name.lower() in item_name):
                item_material_type = normalize_material_type(str(component_material.get("material_type") or material_type), str(component_material.get("material_code") or raw_material_code or ""))
                item_material_code = str(component_material.get("material_code") or raw_material_code or "")
                break
        item_details = MATERIALS[item_material_type]
        item_material_label = str(item_details["label"])
        item_density = float(item_details["density"])
        item_material_rate = float(item_details["default_rate"]) if item_material_type != material_type else active_material_rate
        if item_material_type != material_type:
            if "square tube" in lower_name:
                weight = square_tube_weight(square_tube_outer_mm, square_tube_thickness_mm, square_tube_length_mm, item_density)
            elif "bottom plate" in lower_name:
                weight = plate_weight(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, item_density)
            elif "top plate" in lower_name:
                weight = plate_weight(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, item_density)
            elif "handle" in lower_name:
                weight = round_tube_weight(handle_od_mm, handle_thickness_mm, handle_length_mm, item_density)
            elif "screwing" in lower_name:
                weight = rod_weight(screw_piece_dia_mm, screw_piece_length_mm, item_density) * quantity
        material_cost = weight * item_material_rate
        total_weight += weight
        total_material_cost += material_cost
        stock_info = None
        stock_form = None
        stock_size = None
        if "square tube" in lower_name:
            stock_info = rod_stock_summary(weight, square_tube_length_mm, quantity)
            stock_form = "rod/profile"
            stock_size = f"{int(ROD_STOCK_LENGTH_MM)} mm"
        elif "handle" in lower_name:
            stock_info = rod_stock_summary(weight, handle_length_mm, quantity)
            stock_form = "rod/profile"
            stock_size = f"{int(ROD_STOCK_LENGTH_MM)} mm"
        elif "screwing" in lower_name:
            stock_info = rod_stock_summary(weight / max(quantity, 1), screw_piece_length_mm, quantity)
            stock_form = "rod/profile"
            stock_size = f"{int(ROD_STOCK_LENGTH_MM)} mm"
        elif "angle" in lower_name:
            stock_info = rod_stock_summary(weight, chair_angle_length_mm, quantity)
            stock_form = "rod/profile"
            stock_size = f"{int(ROD_STOCK_LENGTH_MM)} mm"
        elif "top plate" in lower_name:
            stock_info = sheet_nesting_summary(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, item_density, quantity)
            stock_form = "blank sheet"
            stock_size = f"{int(SHEET_STOCK_LENGTH_MM)} x {int(SHEET_STOCK_WIDTH_MM)} mm"
        elif "bottom plate" in lower_name:
            stock_info = sheet_nesting_summary(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, item_density, quantity)
            stock_form = "blank sheet"
            stock_size = f"{int(SHEET_STOCK_LENGTH_MM)} x {int(SHEET_STOCK_WIDTH_MM)} mm"

        scrap_weight = float(stock_info["scrap_weight_kg"]) if stock_info else 0.0
        scrap_value = scrap_weight * SCRAP_RATE_PER_KG
        total_scrap_weight += scrap_weight
        total_scrap_value += scrap_value
        stock_weight = float(stock_info["stock_weight_kg"]) if stock_info else 0.0
        weight_formula = find_step(calculation_steps, weight_step_name)
        material_formula = CalculationStep(
            section="Cost",
            name=f"{name} material cost",
            formula="Material cost = item weight x material rate",
            substituted_values=f"{round(weight, 3)} kg x {CURRENCY_UNIT} {item_material_rate}/kg",
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
                material_type=item_material_type,
                material_label=item_material_label,
                material_rate_per_kg=item_material_rate,
                stock_form=stock_form,
                stock_size=stock_size,
                parts_per_stock=int(stock_info["parts_per_stock"]) if stock_info else None,
                stock_weight_kg=round(stock_weight, 3) if stock_info else None,
                gross_stock_cost=round_money(stock_weight * item_material_rate) if stock_info else None,
                scrap_weight_kg=round(scrap_weight, 3),
                scrap_value=round_money(scrap_value),
                net_stock_cost_per_part=round_money(((stock_weight * item_material_rate) - scrap_value) / max(int(stock_info["parts_per_stock"]), 1)) if stock_info else None,
                nesting_approach=str(stock_info["approach"]) if stock_info else None,
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
                substituted_values=f"{round(total_weight, 3)} kg x {CURRENCY_UNIT} {active_material_rate}/kg",
                result=money(total_material_cost),
            ),
            CalculationStep(
                section="Stock",
                name="Scrap value",
                formula="Scrap value = Scrap weight x scrap rate",
                substituted_values=f"{round(total_scrap_weight, 3)} kg x {CURRENCY_UNIT} {SCRAP_RATE_PER_KG}/kg",
                result=money(total_scrap_value),
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
        material_summary=MaterialSummary(
            material_type=material_type,
            material_label=material_label,
            material_code=raw_material_code,
            density_kg_per_mm3=density,
            rate_per_kg=active_material_rate,
            default_rate_per_kg=default_rate,
            source="extracted/raw-material-code" if raw_material_code else "user/default",
        ),
        stock_summary=StockSummary(
            total_scrap_weight_kg=round(total_scrap_weight, 3),
            total_scrap_value=round_money(total_scrap_value),
            approach="Rod/profile items use 6000 mm linear nesting. Plate/sheet items use 2500 x 1250 mm two-orientation rectangular grid nesting. Mixed-shape CNC nesting is still an estimate and should be checked by the vendor.",
        ),
        assumptions=[
            f"Dimension extraction is handled by Gemini API with {model}; this costing step uses the submitted field values.",
            f"Material is treated as {material_label} ({raw_material_code or material_type}) at {CURRENCY_UNIT} {active_material_rate}/kg; this can be overridden from UI.",
            f"Default material rates: MS {RATE_MS_PER_KG}/kg, SS {RATE_SS_PER_KG}/kg, aluminium {RATE_ALUMINIUM_PER_KG}/kg, copper {RATE_COPPER_PER_KG}/kg.",
            f"Default stock sizes: rod/profile {ROD_STOCK_LENGTH_MM:.0f} mm and sheet/plate {SHEET_STOCK_LENGTH_MM:.0f} x {SHEET_STOCK_WIDTH_MM:.0f} mm.",
            f"Scrap/offcut value is estimated at {CURRENCY_UNIT} {SCRAP_RATE_PER_KG}/kg.",
            "Tax, packaging, transport, and supplier MOQ are not included.",
            "Chair angle weight uses kg/m x length because the detailed LS10269 geometry is not present in the upload.",
            f"Process cost includes laser cutting ({cutting_length_mm} mm across {cutting_surface_count} surfaces), bending ({bend_count} bends), welding ({weld_length_mm} mm), press hits ({press_machine_hits}), painting, and optional tacking labor.",
        ],
        items=line_items,
        process_breakdown=ProcessBreakdown(
            cutting_cost=round_money(cutting_cost),
            bending_cost=round_money(bending_cost),
            welding_cost=round_money(welding_cost),
            press_machine_cost=round_money(press_machine_cost),
            painting_cost=round_money(surface_cost),
            tacking_cost=round_money(tacking_cost),
            cutting_length_mm=cutting_length_mm,
            cutting_surface_count=cutting_surface_count,
            bend_count=bend_count,
            weld_length_mm=weld_length_mm,
            press_machine_hits=press_machine_hits,
        ),
        calculation_steps=calculation_steps,
    )
