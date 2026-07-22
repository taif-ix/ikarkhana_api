from __future__ import annotations

import io
import importlib.util
import json
import os
import re

from fastapi import HTTPException

from app.core.config import ALLOWED_GEMINI_MODELS
from app.models.schemas import ExtractedDimensions, ReferenceExtraction, StructuredExtraction


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
  "referenced_drawings": [
    {
      "drawing_number": string,
      "file_name_hint": string or null,
      "referenced_by_part_number": string or null,
      "referenced_by_component": string or null,
      "reason": string,
      "required_for_costing": true
    }
  ],
  "confidence": number,
  "notes": []
}

Extraction rules:
- Extract all visible BOM/detail-table parts, not just the main tube.
- Detect child/detail drawing references from BOM/detail drawing columns, notes, remarks, or callouts. Example: if CHAIR ANGLE-RH references LS10269, add it to referenced_drawings with drawing_number "LS10269", file_name_hint "LS10269.tif", referenced_by_component "CHAIR ANGLE-RH", and reason explaining which dimensions/features may be missing.
- Do not add the current drawing number itself to referenced_drawings.
- If a referenced child drawing is needed to verify missing geometry, bend count, cut length, holes, or weight, required_for_costing must be true.
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


REFERENCE_EXTRACTION_PROMPT = """
You are a deterministic engineering drawing reference scanner.
Return only valid compact JSON. Do not use markdown.

Extract only drawing/document references printed on this engineering drawing.
Look in BOM rows, DETAIL DRG columns, remarks, notes, callouts, and referenced drawing lists.
References may be drawing numbers such as LS10267, LS10268, LS10269, MDG0008, or any similar printed document/detail number.
They may appear without a file extension.

Return this exact JSON shape:
{
  "drawing_number": string or null,
  "file_name_hint": string or null,
  "referenced_drawings": [
    {
      "drawing_number": string,
      "file_name_hint": string,
      "referenced_by_part_number": string or null,
      "referenced_by_component": string or null,
      "reason": string,
      "required_for_costing": true
    }
  ],
  "confidence": number,
  "notes": []
}

Rules:
- Extract every printed child/detail/reference drawing number exactly as shown.
- Strongly inspect the BOM/table columns named DETAIL DRG, DETAIL DRAWING, DRG NO, CHILD DRG, REF DRG, DRAWING NO, and REMARKS.
- In BOM rows, treat the DESCRIPTION & DIMENSIONS column as the component name and the DETAIL DRG / REMARKS drawing-number columns as possible child references.
- Material/specification values like RDSO/SPEC, C-K201, Gr. 304, ASTM-A312, IS:6603-01, X04Cr12, and NIL are not child drawing numbers unless they appear in a detail drawing/reference column with a drawing-number pattern.
- A valid child drawing usually looks like LS followed by digits, MDG followed by digits, or another explicit drawing/document number printed as a reference.
- file_name_hint should be drawing_number + ".tif" unless another extension is explicitly printed.
- Do not include the current drawing itself in referenced_drawings.
- Do not invent dependencies. If no child/detail drawing number is visible, return an empty referenced_drawings list.
- If a BOM/detail drawing column says NIL, it is not a child drawing.
- If a BOM/detail drawing column contains a drawing number for a component row, include it and set referenced_by_component from that row.
- required_for_costing should be true when the referenced drawing likely contains missing dimensions, flat pattern, bend data, holes, or child geometry.
"""

BOM_REFERENCE_EXTRACTION_PROMPT = """
You are reading only the BOM / parts list / title block area of an engineering drawing.
Return only valid compact JSON. Do not use markdown.

Focus on the tabular rows with headers like:
ITEM, DESCRIPTION & DIMENSIONS, QPASSY/QTY, DETAIL DRG, MATL. & SPEC., REMARKS.

The important dependency column is DETAIL DRG.
For each row:
- If DETAIL DRG contains NIL, ignore it.
- If DETAIL DRG contains a drawing number like LS10268, LS10269, LS10267, MDG0008, include it.
- Use the row ITEM as referenced_by_part_number.
- Use DESCRIPTION & DIMENSIONS as referenced_by_component.
- Do not treat MATL. & SPEC. values as dependencies.
- Do not treat RDSO/SPEC, C-K201, Gr. 304, ASTM-A312, IS:6603-01, X04Cr12, or NIL as dependencies.

Example:
ITEM 4 | CHAIR ANGLE-LH | 1 | LS10268 | NIL | NIL
must return drawing_number "LS10268", file_name_hint "LS10268.tif",
referenced_by_part_number "4", referenced_by_component "CHAIR ANGLE-LH".

Return this exact JSON shape:
{
  "drawing_number": string or null,
  "file_name_hint": string or null,
  "referenced_drawings": [
    {
      "drawing_number": string,
      "file_name_hint": string,
      "referenced_by_part_number": string or null,
      "referenced_by_component": string or null,
      "reason": string,
      "required_for_costing": true
    }
  ],
  "confidence": number,
  "notes": []
}

If no real drawing number is visible in DETAIL DRG / reference columns, return an empty referenced_drawings list.
"""


REFERENCE_NUMBER_PATTERN = re.compile(r"\b(?:LS\d{4,6}[A-Z]?|MDG\d{3,6}|[A-Z]{2,5}\d{3,6}[A-Z]?)\b", re.IGNORECASE)
INVALID_REFERENCE_TOKENS = {
    "NIL",
    "RDSO",
    "SPEC",
    "RDSO/SPEC",
    "C-K201",
    "CK201",
    "K201",
    "GR304",
    "GR.304",
    "304",
    "316",
    "ASTM",
}


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


def _preprocess_image(
    content: bytes,
    content_type: str | None,
    filename: str | None = None,
    *,
    max_side_px: int,
) -> tuple[bytes, str]:
    mime_type = content_type or "application/octet-stream"
    filename = filename or ""
    is_supported_bitmap = (
        mime_type in {"image/tiff", "image/tif", "image/png", "image/jpeg", "image/jpg"}
        or filename.lower().endswith((".tif", ".tiff", ".png", ".jpg", ".jpeg"))
    )
    if not is_supported_bitmap:
        return content, mime_type

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Pillow is required to convert and optimize drawing images.") from exc

    with Image.open(io.BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("L")
        image = ImageOps.autocontrast(image)
        image = image.convert("RGB")
        image.thumbnail((max_side_px, max_side_px), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "image/png"


def image_bytes_for_gemini(content: bytes, content_type: str | None) -> tuple[bytes, str]:
    return _preprocess_image(content, content_type, max_side_px=2200)


def image_bytes_for_preview(content: bytes, content_type: str | None, filename: str | None) -> tuple[bytes, str]:
    return _preprocess_image(content, content_type, filename, max_side_px=3200)


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


def _gemini_generate_json(
    content: bytes,
    content_type: str | None,
    prompt: str,
    child_drawings: list[tuple[str, bytes, str | None]] | None = None,
    *,
    max_side_px: int = 2200,
) -> dict:
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

    image_content, mime_type = _preprocess_image(content, content_type, max_side_px=max_side_px)
    client = genai.Client(vertexai=True, project=project, location=location) if provider == "vertex_ai" else genai.Client(api_key=api_key)
    contents: list[object] = [
        "Main uploaded engineering drawing:",
        types.Part.from_bytes(data=image_content, mime_type=mime_type),
    ]
    for filename, child_content, child_content_type in child_drawings or []:
        child_image_content, child_mime_type = _preprocess_image(child_content, child_content_type, max_side_px=max_side_px)
        contents.extend(
            [
                f"Referenced child/detail drawing file: {filename}",
                types.Part.from_bytes(data=child_image_content, mime_type=child_mime_type),
            ]
        )
    contents.append(prompt)

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
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


def normalize_structured_payload(payload: dict) -> dict:
    parts = payload.get("per_part_breakdown")
    if not isinstance(parts, list):
        payload["per_part_breakdown"] = []
        return payload

    nested_defaults = {
        "dimensions": {},
        "image_region": {},
        "cutting_metrics": {},
        "nesting_layout_hint": {},
    }
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key, default in nested_defaults.items():
            if part.get(key) is None:
                part[key] = default.copy()
        if part.get("notes") is None:
            part["notes"] = []
    return payload


def normalize_reference_extraction(extraction: ReferenceExtraction, filename: str | None = None) -> ReferenceExtraction:
    current_numbers = {
        value.strip().upper().replace("-", "")
        for value in [
            extraction.drawing_number or "",
            (extraction.file_name_hint or "").rsplit(".", 1)[0],
            (filename or "").rsplit(".", 1)[0],
        ]
        if value
    }
    cleaned = []
    seen = set()

    for reference in extraction.referenced_drawings:
        raw_number = str(reference.drawing_number or "").strip()
        raw_hint = str(reference.file_name_hint or "").strip()
        candidate_text = f"{raw_number} {raw_hint}"
        match = REFERENCE_NUMBER_PATTERN.search(candidate_text)
        if not match:
            continue

        drawing_number = match.group(0).upper()
        normalized_number = drawing_number.replace("-", "")
        if normalized_number in current_numbers or normalized_number in INVALID_REFERENCE_TOKENS:
            continue

        file_name_hint = raw_hint if raw_hint and raw_hint.lower() != "none" else f"{drawing_number}.tif"
        if "." not in file_name_hint:
            file_name_hint = f"{drawing_number}.tif"
        if not file_name_hint.lower().startswith(drawing_number.lower()):
            file_name_hint = f"{drawing_number}.tif"

        dedupe_key = normalized_number
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        reference.drawing_number = drawing_number
        reference.file_name_hint = file_name_hint
        reference.required_for_costing = True
        cleaned.append(reference)

    extraction.referenced_drawings = cleaned
    return extraction


def extract_structured_with_gemini(
    content: bytes,
    content_type: str | None,
    child_drawings: list[tuple[str, bytes, str | None]] | None = None,
) -> StructuredExtraction:
    try:
        payload = _gemini_generate_json(content, content_type, STRUCTURED_EXTRACTION_PROMPT, child_drawings)
        return StructuredExtraction.model_validate(normalize_structured_payload(payload))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as structured extraction JSON: {exc}") from exc


def extract_references_with_gemini(content: bytes, content_type: str | None, filename: str | None = None) -> ReferenceExtraction:
    try:
        extraction = ReferenceExtraction.model_validate(
            _gemini_generate_json(content, content_type, REFERENCE_EXTRACTION_PROMPT, max_side_px=3400)
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as reference JSON: {exc}") from exc

    extraction = normalize_reference_extraction(extraction, filename)
    if extraction.referenced_drawings:
        return extraction

    try:
        bom_extraction = ReferenceExtraction.model_validate(
            _gemini_generate_json(content, content_type, BOM_REFERENCE_EXTRACTION_PROMPT, max_side_px=3800)
        )
    except (json.JSONDecodeError, ValueError):
        return extraction

    bom_extraction = normalize_reference_extraction(bom_extraction, filename)
    if bom_extraction.referenced_drawings:
        return bom_extraction
    return extraction
