from __future__ import annotations

import io
import importlib.util
import json
import os
import re

from fastapi import HTTPException

from app.core.config import ALLOWED_GEMINI_MODELS
from app.models.schemas import ExtractedDimensions, StructuredExtraction


EXTRACTION_PROMPT = """
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

STRUCTURED_EXTRACTION_PROMPT = """
You are a deterministic sheet metal feature extraction engine.
You are looking at this engineering drawing to pull raw parameters for a cost calculation.
Return only a compact valid JSON object. Do not use markdown. Do not add comments.
Use double quotes for all keys and valid JSON arrays/objects.

STRICT RULES:
1. Extract ONLY the literal text, numbers, dimensions, notes, symbols, and geometry explicitly printed on the document.
2. If a specific dimension, feature count, sheet thickness, wall thickness, outer flange width, hole diameter, bend count, weld length, or material code is missing, blurry, hidden, or overlapping with another line, do NOT guess, extrapolate, infer, or estimate it.
3. For any text field you cannot verify with 100% certainty, output exactly "NULL - Insufficient Data".
4. For any numeric field you cannot verify with 100% certainty, output null and add "NULL - Insufficient Data" in that part's notes.
5. Do not calculate costs, weights, scrap, or painting. Backend will calculate those from verified inputs only.

Return this exact top-level shape:
{
  "currency": "INR",
  "part_name": string or null,
  "raw_material_type": "ms" | "ss" | "aluminium" | "copper" | "unknown",
  "raw_material_code": string or null,
  "per_part_breakdown": [
    {
      "part_number": string,
      "component_name": string or null,
      "component_type": "tube" | "sheet" | "rod" | "accessory" | "unknown",
      "tube_type": string,
      "material_type": "ms" | "ss" | "aluminium" | "copper" | "unknown" | null,
      "material_code": string or null,
      "per_set_qty": number,
      "dimensions": {
        "length_mm": number or null,
        "width_or_outer_dia_mm": number or null,
        "secondary_width_mm": number or null,
        "thickness_or_wall_thickness_mm": number or null
      },
      "image_region": {
        "x_min": number or null,
        "y_min": number or null,
        "x_max": number or null,
        "y_max": number or null,
        "source": string
      },
      "bends_per_part": number,
      "cutting_metrics": {
        "laser_cutting_length_mm": number,
        "press_machine_hits_count": number
      },
      "nesting_layout_hint": {
        "nesting_strategy": string,
        "recommended_grain_or_cut_direction": string
      },
      "notes": []
    }
  ],
  "assembly_level_fabrication": {
    "total_assembly_welding_length_mm": number,
    "notes": []
  },
  "confidence": number,
  "notes": []
}

Extraction rules:
- Extract all visible BOM/detail-table parts, not just the main tube.
- Do not calculate costs, weights, scrap, or painting. Backend will calculate those.
- Use null where dimensions are not visible.
- For square tube 45x45x4, component_type is tube, tube_type is "Square 45x45x4", width_or_outer_dia_mm is 45, secondary_width_mm is 45, thickness is 4.
- For round tube Dia 19x2, width_or_outer_dia_mm is 19 and thickness is 2.
- For a rectangular/square sheet or plate, length and width go into length_mm and width_or_outer_dia_mm; thickness goes into thickness_or_wall_thickness_mm.
- For a rod/bar/accessory, length goes into length_mm and diameter/outer size goes into width_or_outer_dia_mm.
- image_region is the approximate visible drawing/detail region for that specific part, not the whole page.
- image_region coordinates must be normalized from 0 to 1000 relative to the full drawing image: x_min/y_min is top-left, x_max/y_max is bottom-right.
- Use only the actual drawing/detail geometry region for image_region. Never use the BOM/table row, title block, material table, or text-only row as image_region.
- If only the BOM row identifies the part and no specific drawing/detail geometry can be verified, set all image_region coordinates to null and source to "NULL - Insufficient Data".
- If the specific part location cannot be verified, set all image_region coordinates to null and source to "NULL - Insufficient Data".
- Detect bends per part from bend/fold/formed angle/tube bend indications.
- laser_cutting_length_mm is the perimeter/profile cut length visible for that part. Rectangular perimeter = 2 x (L + W). Circular cut = pi x diameter.
- press_machine_hits_count is number of punched/pressed cut surfaces/features if visible. If unclear, use 0 and explain in notes.
- total_assembly_welding_length_mm should come from visible weld symbols/locations; if unclear estimate from joint perimeters and explain in notes.
- Material detection: C-K201/K201/304/316/stainless means ss. MS/IS2062/E250/E350 means ms. AL/6061/6082 means aluminium. CU/copper/C11000 means copper.
"""


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
    provider = os.getenv("GEMINI_PROVIDER", "gemini_api").lower()
    api_key = os.getenv("GEMINI_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

    if provider not in {"gemini_api", "vertex_ai"}:
        raise HTTPException(status_code=503, detail="GEMINI_PROVIDER must be gemini_api or vertex_ai.")
    if provider == "gemini_api" and (not api_key or api_key == "your-gemini-api-key"):
        raise HTTPException(status_code=503, detail="Set GEMINI_API_KEY to call Gemini API for dimension extraction.")
    if provider == "vertex_ai" and not project:
        raise HTTPException(status_code=503, detail="Set GOOGLE_CLOUD_PROJECT to call Gemini through Vertex AI.")
    if model not in ALLOWED_GEMINI_MODELS:
        raise HTTPException(status_code=503, detail=f"GEMINI_MODEL must be one of: {', '.join(sorted(ALLOWED_GEMINI_MODELS))}.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Install google-genai to enable Gemini API extraction.") from exc

    image_content, mime_type = image_bytes_for_gemini(content, content_type)
    client = genai.Client(vertexai=True, project=project, location=location) if provider == "vertex_ai" else genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=image_content, mime_type=mime_type), EXTRACTION_PROMPT],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
    except Exception as exc:
        message = str(exc)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            raise HTTPException(status_code=401, detail="Gemini API key is invalid. Create/copy a valid key from Google AI Studio and update GEMINI_API_KEY.") from exc
        if "RESOURCE_EXHAUSTED" in message or "Quota exceeded" in message:
            raise HTTPException(status_code=429, detail=f"Gemini quota is exhausted for model {model}. Try another available model, wait for quota reset, or enable billing/quota.") from exc
        if "NOT_FOUND" in message or "no longer available" in message:
            raise HTTPException(status_code=404, detail=f"Gemini model {model} is not available for this project/API key.") from exc
        if "BILLING_DISABLED" in message or "requires billing to be enabled" in message:
            raise HTTPException(status_code=402, detail="This Gemini request requires billing for the selected Google project.") from exc
        if "SERVICE_DISABLED" in message:
            raise HTTPException(status_code=503, detail="Gemini/Vertex AI API is disabled for this project. Enable the API, then retry.") from exc
        if "PERMISSION_DENIED" in message:
            raise HTTPException(status_code=403, detail=f"Gemini API permission denied: {message}") from exc
        raise HTTPException(status_code=502, detail=f"Gemini API extraction failed: {message}") from exc

    if not response.text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty extraction response.")

    try:
        extracted = ExtractedDimensions.model_validate(clean_json_response(response.text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as extraction JSON: {exc}") from exc

    extracted.source = "gemini_api"
    return extracted


def _gemini_generate_json(content: bytes, content_type: str | None, prompt: str) -> dict:
    provider = os.getenv("GEMINI_PROVIDER", "gemini_api").lower()
    api_key = os.getenv("GEMINI_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

    if provider not in {"gemini_api", "vertex_ai"}:
        raise HTTPException(status_code=503, detail="GEMINI_PROVIDER must be gemini_api or vertex_ai.")
    if provider == "gemini_api" and (not api_key or api_key == "your-gemini-api-key"):
        raise HTTPException(status_code=503, detail="Set GEMINI_API_KEY to call Gemini API for dimension extraction.")
    if provider == "vertex_ai" and not project:
        raise HTTPException(status_code=503, detail="Set GOOGLE_CLOUD_PROJECT to call Gemini through Vertex AI.")
    if model not in ALLOWED_GEMINI_MODELS:
        raise HTTPException(status_code=503, detail=f"GEMINI_MODEL must be one of: {', '.join(sorted(ALLOWED_GEMINI_MODELS))}.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Install google-genai to enable Gemini API extraction.") from exc

    image_content, mime_type = image_bytes_for_gemini(content, content_type)
    client = genai.Client(vertexai=True, project=project, location=location) if provider == "vertex_ai" else genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=image_content, mime_type=mime_type), prompt],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
    except Exception as exc:
        message = str(exc)
        if "SERVICE_DISABLED" in message:
            raise HTTPException(status_code=503, detail="Gemini/Vertex AI API is disabled for this project. Enable the API, then retry.") from exc
        if "BILLING_DISABLED" in message or "requires billing to be enabled" in message:
            raise HTTPException(status_code=402, detail="This Gemini request requires billing for the selected Google project.") from exc
        if "PERMISSION_DENIED" in message:
            raise HTTPException(status_code=403, detail=f"Gemini API permission denied: {message}") from exc
        if "RESOURCE_EXHAUSTED" in message or "Quota exceeded" in message:
            raise HTTPException(status_code=429, detail=f"Gemini quota is exhausted for model {model}.") from exc
        raise HTTPException(status_code=502, detail=f"Gemini API extraction failed: {message}") from exc

    if not response.text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty extraction response.")
    return clean_json_response(response.text)


def extract_structured_with_gemini(content: bytes, content_type: str | None) -> StructuredExtraction:
    try:
        return StructuredExtraction.model_validate(_gemini_generate_json(content, content_type, STRUCTURED_EXTRACTION_PROMPT))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as structured extraction JSON: {exc}") from exc
