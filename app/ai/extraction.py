import copy
import hashlib
import io
import json
import os
from typing import Any, Dict

from PIL import Image
from google.genai import types

from app import state
from app.ai.prompts import DRAWING_EXTRACTION_PROMPT
from app.ai.schemas import ExtractedBOMAssembly
from app.services.costing import premium_calculate_advanced_geometries, premium_generate_process_sequence_advisory


# Gemini/Vertex AI drawing extraction and calibration logic.
# =====================================================================
async def async_analyze_single_drawing(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    if file_hash in state.GLOBAL_BLUEPRINT_CACHE:
        return copy.deepcopy(state.GLOBAL_BLUEPRINT_CACHE[file_hash])

    file_ext = filename.split(".")[-1].lower()
    if file_ext in ["tif", "tiff"]:
        with Image.open(io.BytesIO(file_bytes)) as img:
            rgb_img = img.convert("RGB")
            buffer = io.BytesIO()
            rgb_img.save(buffer, format="JPEG", quality=80)
            processed_bytes = buffer.getvalue()
            mime_type = "image/jpeg"
    else:
        processed_bytes = file_bytes
        mime_type = "application/pdf" if file_ext == "pdf" else f"image/{file_ext}"

    drawing_part = types.Part.from_bytes(data=processed_bytes, mime_type=mime_type)

    prompt = DRAWING_EXTRACTION_PROMPT

    response = await state.client.aio.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        contents=[prompt, drawing_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedBOMAssembly,
            temperature=0.0
        ),
    )
    
    bom_dataset = json.loads(response.text)
    drg_id = bom_dataset.get("current_drawing_id", filename.split(".")[0]).strip().upper()
    target_wt = bom_dataset.get("target_blueprint_weight_kg", None)
    
    STEEL_DENSITY_KG_MM3 = 7.85 / 1000000.0
    uncalibrated_components = []
    total_uncalibrated_net_wt = 0.0

    for item in bom_dataset.get("components", []):
        qty = item.get("per_set_qty", 1)
        comp_type = item.get("component_type", "sheet").lower()
        L = item.get("part_length_mm", 0.0)
        W = item.get("part_width_mm", 0.0)
        t = item.get("thickness_mm", 0.0)
        slots = item.get("estimated_punched_slots_count", 0)
        is_tapered = item.get("is_tapered_profile", False)

        if comp_type == "perforated_tray":
            raw_volume = L * W * t
            punched_void_volume = slots * (15.0 * 6.0 * t) if slots > 0 else 0.0
            net_volume = max(raw_volume * 0.75, raw_volume - punched_void_volume)
            n_wt = net_volume * STEEL_DENSITY_KG_MM3
            g_wt = n_wt * 1.05
        elif comp_type == "tapered_gusset" or is_tapered:
            net_volume = (L * W * t) * 0.50
            n_wt = net_volume * STEEL_DENSITY_KG_MM3
            g_wt = n_wt * 1.06
        elif comp_type == "handle":
            net_volume = L * W * t * 0.8
            n_wt = net_volume * STEEL_DENSITY_KG_MM3
            g_wt = n_wt * 1.05
        elif comp_type == "screwing_piece":
            radius_mm = W / 2.0
            net_volume = 3.14159 * (radius_mm ** 2) * L
            n_wt = net_volume * STEEL_DENSITY_KG_MM3
            g_wt = n_wt * 1.05
        else:
            net_volume = L * W * t
            n_wt = net_volume * STEEL_DENSITY_KG_MM3
            g_wt = n_wt * 1.05

        total_uncalibrated_net_wt += (n_wt * qty)
        uncalibrated_components.append({
            "item": item, "n_wt": n_wt, "g_wt": g_wt, "comp_type": comp_type, "L": L, "W": W, "t": t, "slots": slots, "is_tapered": is_tapered
        })

    calibration_factor = 1.0
    if target_wt and target_wt > 0 and total_uncalibrated_net_wt > 0:
        calibration_factor = target_wt / total_uncalibrated_net_wt

    calibrated_components = []
    final_gross_accumulator = 0.0
    final_net_accumulator = 0.0

    for uc in uncalibrated_components:
        calib_n_wt = uc["n_wt"] * calibration_factor
        calib_g_wt = uc["g_wt"] * calibration_factor
        qty = uc["item"].get("per_set_qty", 1)
        
        final_net_accumulator += (calib_n_wt * qty)
        final_gross_accumulator += (calib_g_wt * qty)
        
        geom_metrics = premium_calculate_advanced_geometries(uc["comp_type"], uc["L"], uc["W"], bom_dataset.get("total_estimated_welding_length_mm", 0.0), uc["is_tapered"])
        advisory_msg = premium_generate_process_sequence_advisory(uc["comp_type"], uc["slots"], uc["item"].get("number_of_bends_per_part", 0), uc["is_tapered"])
        
        calibrated_components.append({
            "part_number": uc["item"].get("part_number", "UNKNOWN"),
            "component_type": uc["comp_type"],
            "description": uc["item"].get("description", ""),
            "per_set_qty": qty,
            "length_mm": uc["L"],
            "width_mm": uc["W"],
            "thickness_mm": uc["t"],
            "bends": uc["item"].get("number_of_bends_per_part", 0),
            "slots_count": uc["slots"],
            "is_tapered": uc["is_tapered"],
            "position_z_mm": uc["item"].get("position_z_mm", 5.0),
            "net_weight_kg": calib_n_wt,
            "scrap_weight_kg": max(0.0, calib_g_wt - calib_n_wt),
            "gross_weight_kg": calib_g_wt,
            "laser_cutting_length_mm": geom_metrics["cutting_length_mm"],
            "laser_welding_length_mm": geom_metrics["welding_length_mm"],
            "process_sequence": advisory_msg
        })

    compiled_result = {
        "drawing_id": drg_id,
        "target_blueprint_weight_kg": target_wt if target_wt else total_uncalibrated_net_wt,
        "calculated_net_wt": round(final_net_accumulator, 3),
        "calculated_gross_wt": round(final_gross_accumulator, 3),
        "components": calibrated_components,
        "total_estimated_welding_length_mm": bom_dataset.get("total_estimated_welding_length_mm", 0),
        "referenced_drawing_ids": bom_dataset.get("referenced_drawing_ids", [])
    }
    
    state.GLOBAL_BLUEPRINT_CACHE[file_hash] = copy.deepcopy(compiled_result)
    return compiled_result

# =====================================================================
# 6. APPLICATION WORKSPACE INTERFACE DEPLOYMENT
# =====================================================================
