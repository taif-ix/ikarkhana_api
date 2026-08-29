from __future__ import annotations

import io
import importlib.util
import json
import os
import re

from fastapi import HTTPException

from app.core.config import ALLOWED_GEMINI_MODELS
from app.diagnostics import cloud_print
from app.models.schemas import ReferenceExtraction, StructuredExtraction


STRUCTURED_EXTRACTION_PROMPT = """
Extract only verified calculation inputs from the engineering drawing and return valid JSON with exactly this shape:
{"raw_material_type":string|null,"raw_material_code":string|null,"per_part_breakdown":[{"part_number":string,"component_name":string|null,"component_type":"tube"|"sheet"|"rod"|"accessory"|"unknown","profile":{"shape":string,"is_hollow":boolean}|null,"material_type":string|null,"material_code":string|null,"material_grade":string|null,"material_specification":string|null,"per_set_qty":number,"dimensions":{"length_mm":number|null,"width_mm":number|null,"height_mm":number|null,"outer_diameter_mm":number|null,"thickness_mm":number|null},"holes":[{"hole_type":string,"diameter_mm":number|null,"quantity_per_part":number|null,"through":boolean|null}],"slots":[{"slot_type":string,"length_mm":number|null,"width_mm":number|null,"quantity_per_part":number|null,"through":boolean|null}],"threads":[{"thread_size":string|null,"nominal_diameter_mm":number|null,"quantity_per_part":number|null,"through":boolean|null,"thread_depth_mm":number|null}],"notches":[],"cutouts":[],"chamfers":[],"bends":[],"flat_pattern":{"outer_contour":{"geometry_type":"polygon","points_mm":[{"x":number,"y":number}]}|null,"holes":[],"slots":[],"threads":[],"notches":[],"cutouts":[],"chamfers":[],"bend_lines":[]}|null,"bends_per_part":number|null,"referenced_drawing_number":string|null,"nesting_constraints":{"grain_direction":string|null,"rotation_allowed":boolean|null,"mirror_pair_required":boolean|null}|null}],"assembly_fabrication":{"welding_length_mm":number|null}}

Rules:
- Preserve part_number and component_name exactly as printed in the BOM. Convert printed NIL to JSON null.
- Populate material_type and material_code for every part. When the drawing specifies one assembly/raw material for all parts and does not show a different part material, copy that drawing-level material into each part.
- Always return bends_per_part as an integer. Return 0 when the part has no visible bend lines, bend callouts, formed profile, or bend operation; otherwise return the verified bend count.
- Extract BOM quantity as per_set_qty, referenced child drawing into referenced_drawing_number, and keep specification, code, and grade separate.
- Use null for every unverified value. Never guess and never replace unknown values with zero.
- Never return a generic features key. Extract holes, slots, threads, notches, cutouts, chamfers, and bends into their named arrays.
- For sheet-metal parts, put developed outer-contour points, holes, slots, and bend lines inside flat_pattern. Do not duplicate those items in the part-level arrays.
- Use part-level geometry arrays for tubes, rods, and other parts without a developed flat pattern.
- nesting_constraints may contain only restrictions explicitly printed on the drawing. Do not recommend a nesting strategy.
- Do not return currency, part name, rates, costs, weights, cutting metrics, process selection, image regions, confidence, sources, or notes.
- Do not calculate laser length or press hits. The backend derives them after user process selection.
"""


REFERENCE_EXTRACTION_PROMPT = """
You are a deterministic engineering drawing reference scanner.
Return only valid compact JSON. Do not use markdown.

Extract only drawing/document references printed on this engineering drawing.
Look in BOM rows, DETAIL DRG columns, remarks, notes, callouts, and referenced drawing lists.

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
- Extract references like LS10267, LS10268, LS10269 exactly when printed.
- file_name_hint should be drawing_number + ".tif" unless another extension is explicitly printed.
- Do not include the current drawing itself in referenced_drawings.
- Do not invent dependencies. If no child/detail drawing number is visible, return an empty referenced_drawings list.
- If a BOM/detail drawing column says NIL, it is not a child drawing.
- required_for_costing should be true when the referenced drawing likely contains missing dimensions, flat pattern, bend data, holes, or child geometry.
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


def log_ai_json_response(*, response_type: str, provider: str, model: str, payload: dict) -> None:
    cloud_print(
        "AI_JSON_RESPONSE",
        message=f"AI JSON RESPONSE | {response_type}",
        ai={
            "provider": provider,
            "model": model,
            "response_type": response_type,
            "response": payload,
        },
    )


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


def _gemini_generate_json(
    content: bytes,
    content_type: str | None,
    prompt: str,
    child_drawings: list[tuple[str, bytes, str | None]] | None = None,
    *,
    response_type: str,
) -> dict:
    provider = os.getenv("GEMINI_PROVIDER", "gemini_api").lower()
    api_key = os.getenv("GEMINI_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")

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
    contents: list[object] = [
        "Main uploaded engineering drawing:",
        types.Part.from_bytes(data=image_content, mime_type=mime_type),
    ]
    for filename, child_content, child_content_type in child_drawings or []:
        child_image_content, child_mime_type = image_bytes_for_gemini(child_content, child_content_type)
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
    response_json = clean_json_response(response.text)
    log_ai_json_response(
        response_type=response_type,
        provider=provider,
        model=model,
        payload=response_json,
    )
    return response_json


def extract_structured_with_gemini(
    content: bytes,
    content_type: str | None,
    child_drawings: list[tuple[str, bytes, str | None]] | None = None,
) -> StructuredExtraction:
    try:
        return StructuredExtraction.model_validate(
            _gemini_generate_json(
                content,
                content_type,
                STRUCTURED_EXTRACTION_PROMPT,
                child_drawings,
                response_type="structured_extraction",
            )
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as structured extraction JSON: {exc}") from exc


def extract_references_with_gemini(content: bytes, content_type: str | None) -> ReferenceExtraction:
    try:
        extraction = ReferenceExtraction.model_validate(
            _gemini_generate_json(
                content,
                content_type,
                REFERENCE_EXTRACTION_PROMPT,
                response_type="reference_extraction",
            )
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned a response that could not be parsed as reference JSON: {exc}") from exc

    current = (extraction.drawing_number or "").strip().lower()
    extraction.referenced_drawings = [
        reference
        for reference in extraction.referenced_drawings
        if reference.drawing_number != "UNKNOWN" and reference.drawing_number.strip().lower() != current
    ]
    return extraction
