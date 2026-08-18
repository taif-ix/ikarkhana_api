from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from app.ai.extraction import async_analyze_single_drawing


router = APIRouter(tags=["structured-costing"])


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def _cost_breakdown(
    extraction: dict[str, Any],
    *,
    material_rate_per_kg: float,
    laser_cutting_rate_per_meter: float,
    press_machine_rate_per_hit: float,
    bend_rate_per_bend: float,
    welding_labor_per_meter: float,
    painting_rate_per_m2: float,
    scrap_rate_per_kg: float,
    tacking_fixed_setup_cost: float,
) -> dict[str, Any]:
    costed_parts = []
    for index, source in enumerate(extraction.get("per_part_breakdown") or [], start=1):
        part = dict(source)
        qty = max(int(_number(part.get("per_set_qty"), 1)), 1)
        dimensions = part.get("dimensions") or {}
        length = _number(dimensions.get("length_mm"))
        width = _number(dimensions.get("width_or_outer_dia_mm"))
        secondary = _number(dimensions.get("secondary_width_mm"), width)
        thickness = _number(dimensions.get("thickness_or_wall_thickness_mm"))
        component_type = str(part.get("component_type") or "sheet").lower()

        ledger = part.get("weight_ledger") or {}
        net_weight = _number(ledger.get("unit_net_finished_weight_kg"))
        if not net_weight:
            # Density of steel in kg/mm3; hollow profiles use the outer-minus-inner area.
            if component_type in {"tube", "pipe", "rod", "profile"}:
                if secondary and abs(secondary - width) > 0.001:
                    outer_area = width * secondary
                    inner_area = max(width - 2 * thickness, 0) * max(secondary - 2 * thickness, 0)
                else:
                    outer_area = width * width
                    inner_area = max(width - 2 * thickness, 0) ** 2
                net_weight = max(outer_area - inner_area, 0) * length * 7.85e-6
            else:
                net_weight = length * width * thickness * 7.85e-6

        gross_weight = _number(ledger.get("unit_gross_rm_weight_kg"), net_weight * 1.05)
        gross_weight = max(gross_weight, net_weight)
        scrap_weight = _number(ledger.get("unit_scrap_waste_weight_kg"), gross_weight - net_weight)
        surface_area = _number(part.get("surface_area_sq_meter"))
        if not surface_area:
            surface_area = (2 * (length * width + length * thickness + width * thickness)) / 1_000_000

        metrics = part.get("cutting_metrics") or {}
        laser_length = _number(metrics.get("laser_cutting_length_mm"))
        press_hits = int(_number(metrics.get("press_machine_hits_count")))
        bends = int(_number(part.get("bends_per_part")))
        material_cost = max(gross_weight * material_rate_per_kg - scrap_weight * scrap_rate_per_kg, 0)
        laser_cost = laser_length / 1000 * laser_cutting_rate_per_meter
        machine_cost = press_hits * press_machine_rate_per_hit
        bending_cost = bends * bend_rate_per_bend
        painting_cost = surface_area * painting_rate_per_m2
        single_laser = material_cost + laser_cost + bending_cost + painting_cost
        single_machine = material_cost + machine_cost + bending_cost + painting_cost

        part.update({
            "part_number": str(part.get("part_number") or index),
            "component_type": component_type,
            "per_set_qty": qty,
            "dimensions": dimensions,
            "surface_area_sq_meter": round(surface_area, 4),
            "weight_ledger": {
                "unit_gross_rm_weight_kg": round(gross_weight, 3),
                "unit_net_finished_weight_kg": round(net_weight, 3),
                "unit_scrap_waste_weight_kg": round(scrap_weight, 3),
                "total_set_gross_weight_kg": round(gross_weight * qty, 3),
            },
            "calculated_costs": {
                "material_cost": round(material_cost, 2),
                "laser_cutting_cost_estimate": round(laser_cost, 2),
                "machine_punching_cost_estimate": round(machine_cost, 2),
                "bending_cost": round(bending_cost, 2),
                "painting_cost": round(painting_cost, 2),
                "total_single_part_cost_via_laser": round(single_laser, 2),
                "total_single_part_cost_via_machine": round(single_machine, 2),
                "total_combined_set_cost_via_laser": round(single_laser * qty, 2),
                "total_combined_set_cost_via_machine": round(single_machine * qty, 2),
            },
            "calculation_steps": [],
        })
        costed_parts.append(part)

    fabrication = extraction.get("assembly_level_fabrication") or {}
    welding_length = _number(fabrication.get("total_assembly_welding_length_mm"))
    welding_cost = welding_length / 1000 * welding_labor_per_meter
    laser_total = sum(p["calculated_costs"]["total_combined_set_cost_via_laser"] for p in costed_parts)
    machine_total = sum(p["calculated_costs"]["total_combined_set_cost_via_machine"] for p in costed_parts)
    return {
        "currency": extraction.get("currency") or "INR",
        "part_name": extraction.get("part_name"),
        "per_part_breakdown": costed_parts,
        "assembly_level_fabrication": {
            "total_assembly_welding_length_mm": round(welding_length, 2),
            "welding_labor_cost": round(welding_cost, 2),
            "tacking_fixed_setup_cost": round(tacking_fixed_setup_cost, 2),
            "grand_total_assembly_cost_via_laser": round(laser_total + welding_cost + tacking_fixed_setup_cost, 2),
            "grand_total_assembly_cost_via_machine": round(machine_total + welding_cost + tacking_fixed_setup_cost, 2),
        },
        "referenced_drawings": extraction.get("referenced_drawings") or [],
        "assumptions": ["Weights and costs are calculated from extracted drawing dimensions and configured rates."],
    }


def _drawing_to_extraction(drawing: dict[str, Any]) -> dict[str, Any]:
    parts = []
    for component in drawing.get("components") or []:
        parts.append({
            "part_number": component.get("part_number") or "UNKNOWN",
            "component_name": component.get("description"),
            "component_type": component.get("component_type") or "sheet",
            "tube_type": component.get("tube_type") or "NA",
            "material_type": component.get("material"),
            "material_code": component.get("material_code"),
            "per_set_qty": component.get("per_set_qty") or 1,
            "dimensions": {
                "length_mm": component.get("length_mm"),
                "width_or_outer_dia_mm": component.get("width_mm"),
                "secondary_width_mm": component.get("height_mm"),
                "thickness_or_wall_thickness_mm": component.get("thickness_mm"),
            },
            "image_region": component.get("image_region") or {},
            "bends_per_part": component.get("bends") or 0,
            "cutting_metrics": {
                "laser_cutting_length_mm": component.get("laser_cutting_length_mm") or 0,
                "press_machine_hits_count": component.get("slots_count") or 0,
            },
            "nesting_layout_hint": component.get("blank_required") or {},
            "notes": [],
            "weight_ledger": {
                "unit_gross_rm_weight_kg": component.get("gross_weight_kg") or 0,
                "unit_net_finished_weight_kg": component.get("net_weight_kg") or 0,
                "unit_scrap_waste_weight_kg": component.get("scrap_weight_kg") or 0,
            },
        })
    return {
        "currency": "INR",
        "part_name": drawing.get("drawing_id"),
        "per_part_breakdown": parts,
        "assembly_level_fabrication": {
            "total_assembly_welding_length_mm": drawing.get("total_estimated_welding_length_mm") or 0,
        },
        "referenced_drawings": [
            {"drawing_number": value, "required_for_costing": True}
            for value in drawing.get("referenced_drawing_ids") or []
        ],
    }


@router.post("/calculate-cost-breakdown")
async def calculate_cost_breakdown(request: dict[str, Any]) -> dict[str, Any]:
    extraction = request.get("extraction") or {}
    rates = {key: _number(request.get(key), default) for key, default in {
        "material_rate_per_kg": 240, "laser_cutting_rate_per_meter": 200,
        "press_machine_rate_per_hit": 5, "bend_rate_per_bend": 2,
        "welding_labor_per_meter": 22, "painting_rate_per_m2": 120,
        "scrap_rate_per_kg": 28, "tacking_fixed_setup_cost": 0,
    }.items()}
    return _cost_breakdown(extraction, **rates)


@router.post("/extract-cost-breakdown")
async def extract_cost_breakdown(
    diagram: UploadFile = File(...),
    child_diagrams: list[UploadFile] | None = File(None),
    material_rate_per_kg: float = Form(240), laser_cutting_rate_per_meter: float = Form(200),
    press_machine_rate_per_hit: float = Form(5), bend_rate_per_bend: float = Form(2),
    welding_labor_per_meter: float = Form(22), painting_rate_per_m2: float = Form(120),
    scrap_rate_per_kg: float = Form(28), tacking_fixed_setup_cost: float = Form(0),
) -> dict[str, Any]:
    drawing = await async_analyze_single_drawing(await diagram.read(), diagram.filename or "uploaded-diagram.png")
    extraction = _drawing_to_extraction(drawing)
    return _cost_breakdown(
        extraction, material_rate_per_kg=material_rate_per_kg,
        laser_cutting_rate_per_meter=laser_cutting_rate_per_meter,
        press_machine_rate_per_hit=press_machine_rate_per_hit, bend_rate_per_bend=bend_rate_per_bend,
        welding_labor_per_meter=welding_labor_per_meter, painting_rate_per_m2=painting_rate_per_m2,
        scrap_rate_per_kg=scrap_rate_per_kg, tacking_fixed_setup_cost=tacking_fixed_setup_cost,
    )
