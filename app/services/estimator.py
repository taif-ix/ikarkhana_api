from __future__ import annotations

import json
import math
import os

from app.core.config import (
    CURRENCY_UNIT,
    LABOR_TACKING_FIXED,
    MATERIALS,
    RATE_ALUMINIUM_PER_KG,
    RATE_COPPER_PER_KG,
    RATE_MS_PER_KG,
    RATE_SS_PER_KG,
    ROD_STOCK_LENGTH_MM,
    SCRAP_RATE_PER_KG,
    SHEET_STOCK_LENGTH_MM,
    SHEET_STOCK_WIDTH_MM,
)
from app.models.schemas import (
    AssemblyLevelFabrication,
    CalculatedCosts,
    CalculationStep,
    CostedPartBreakdown,
    EstimateResponse,
    LineItem,
    MaterialSummary,
    ProcessBreakdown,
    StockSummary,
    StructuredCostBreakdown,
    StructuredExtraction,
    WeightLedger,
)
from app.services.formulas import (
    find_step,
    kg,
    money,
    normalize_material_type,
    plate_steps,
    plate_surface_area_m2,
    plate_weight,
    rod_steps,
    rod_stock_summary,
    rod_weight,
    round_money,
    round_tube_steps,
    round_tube_weight,
    sheet_nesting_summary,
    square_tube_steps,
    square_tube_weight,
    tube_surface_area_m2,
)


def _part_area_and_weight(
    component_type: str,
    tube_type: str,
    length_mm: float,
    width_or_dia_mm: float,
    secondary_width_mm: float | None,
    thickness_mm: float,
    density: float,
) -> tuple[float, float]:
    component = component_type.lower()
    tube = tube_type.lower()
    if component == "tube" and ("round" in tube or "dia" in tube or "ø" in tube):
        area = math.pi / 4 * (width_or_dia_mm**2 - max(width_or_dia_mm - (2 * thickness_mm), 0) ** 2)
        weight = area * length_mm * density
        surface = tube_surface_area_m2(math.pi * width_or_dia_mm, length_mm)
        return surface, weight
    if component == "tube":
        outer_b = secondary_width_mm or width_or_dia_mm
        inner_a = max(width_or_dia_mm - (2 * thickness_mm), 0)
        inner_b = max(outer_b - (2 * thickness_mm), 0)
        area = (width_or_dia_mm * outer_b) - (inner_a * inner_b)
        weight = area * length_mm * density
        surface = tube_surface_area_m2(2 * (width_or_dia_mm + outer_b), length_mm)
        return surface, weight
    if component in {"sheet", "plate"}:
        width = width_or_dia_mm
        weight = plate_weight(length_mm, width, thickness_mm, density)
        surface = plate_surface_area_m2(length_mm, width, thickness_mm)
        return surface, weight
    if component in {"rod", "accessory"}:
        radius = width_or_dia_mm / 2
        weight = rod_weight(width_or_dia_mm, length_mm, density)
        surface = ((2 * math.pi * radius * length_mm) + (2 * math.pi * radius * radius)) / 1_000_000
        return surface, weight
    weight = plate_weight(length_mm, width_or_dia_mm, thickness_mm, density)
    surface = plate_surface_area_m2(length_mm, width_or_dia_mm, thickness_mm)
    return surface, weight


def _stock_for_part(component_type: str, net_weight: float, length_mm: float, width_mm: float, thickness_mm: float, density: float, qty: int) -> tuple[float, float, str]:
    component = component_type.lower()
    if component in {"tube", "rod", "accessory"}:
        stock = rod_stock_summary(net_weight, length_mm, qty)
    else:
        stock = sheet_nesting_summary(length_mm, width_mm, thickness_mm, density, qty)
    parts_per_stock = max(int(stock["parts_per_stock"]), 1)
    gross_unit = float(stock["stock_weight_kg"]) / parts_per_stock
    scrap_unit = max(gross_unit - net_weight, 0)
    return gross_unit, scrap_unit, str(stock["approach"])


def calculate_structured_cost_breakdown(
    extraction: StructuredExtraction,
    *,
    material_rate_per_kg: float | None,
    laser_cutting_rate_per_meter: float,
    press_machine_rate_per_hit: float,
    bend_rate_per_bend: float,
    welding_labor_per_meter: float,
    painting_rate_per_m2: float,
    tacking_fixed_setup_cost: float,
) -> StructuredCostBreakdown:
    material_type = normalize_material_type(extraction.raw_material_type, extraction.raw_material_code)
    default_details = MATERIALS[material_type]
    default_density = float(default_details["density"])
    default_rate = material_rate_per_kg if material_rate_per_kg and material_rate_per_kg > 0 else float(default_details["default_rate"])
    costed_parts: list[CostedPartBreakdown] = []

    for index, part in enumerate(extraction.per_part_breakdown, start=1):
        dims = part.dimensions
        length = float(dims.length_mm or 0)
        width_or_dia = float(dims.width_or_outer_dia_mm or 0)
        secondary_width = dims.secondary_width_mm
        thickness = float(dims.thickness_or_wall_thickness_mm or 0)
        qty = max(int(part.per_set_qty or 1), 1)
        item_material_type = normalize_material_type(part.material_type or material_type, part.material_code or extraction.raw_material_code)
        item_details = MATERIALS[item_material_type]
        density = float(item_details["density"])
        rate = default_rate if item_material_type == material_type else float(item_details["default_rate"])

        surface_area, net_weight = _part_area_and_weight(part.component_type, part.tube_type, length, width_or_dia, secondary_width, thickness, density)
        gross_weight, scrap_weight, stock_approach = _stock_for_part(part.component_type, net_weight, length, width_or_dia, thickness, density, qty)
        laser_cutting_cost = (part.cutting_metrics.laser_cutting_length_mm / 1000) * laser_cutting_rate_per_meter
        machine_punching_cost = part.cutting_metrics.press_machine_hits_count * press_machine_rate_per_hit
        bending_cost = part.bends_per_part * bend_rate_per_bend
        painting_cost = surface_area * painting_rate_per_m2
        material_cost = gross_weight * rate
        single_laser = material_cost + laser_cutting_cost + bending_cost + painting_cost
        single_machine = material_cost + machine_punching_cost + bending_cost + painting_cost

        steps = [
            CalculationStep(
                section="Weight",
                name=f"Part {part.part_number} net weight",
                formula="Net weight = calculated volume x material density",
                substituted_values=f"geometry from {part.component_type}, density {density} kg/mm3",
                result=kg(net_weight),
            ),
            CalculationStep(
                section="Stock",
                name=f"Part {part.part_number} gross RM weight",
                formula="Gross unit RM weight = stock weight / parts per stock",
                substituted_values=stock_approach,
                result=kg(gross_weight),
            ),
            CalculationStep(
                section="Cost",
                name=f"Part {part.part_number} material cost",
                formula="Material cost = gross RM weight x material rate",
                substituted_values=f"{round(gross_weight, 3)} kg x {CURRENCY_UNIT} {rate}/kg",
                result=money(material_cost),
            ),
        ]

        part_payload = part.model_dump()
        part_payload["part_number"] = part.part_number or str(index)
        part_payload["nesting_layout_hint"] = part.nesting_layout_hint.model_copy(
            update={
                "nesting_strategy": part.nesting_layout_hint.nesting_strategy or stock_approach,
            }
        )
        costed_parts.append(
            CostedPartBreakdown(
                **part_payload,
                surface_area_sq_meter=round(surface_area, 4),
                weight_ledger=WeightLedger(
                    unit_gross_rm_weight_kg=round(gross_weight, 3),
                    unit_net_finished_weight_kg=round(net_weight, 3),
                    unit_scrap_waste_weight_kg=round(scrap_weight, 3),
                    total_set_gross_weight_kg=round(gross_weight * qty, 3),
                ),
                calculated_costs=CalculatedCosts(
                    material_cost=round_money(material_cost),
                    laser_cutting_cost_estimate=round_money(laser_cutting_cost),
                    machine_punching_cost_estimate=round_money(machine_punching_cost),
                    bending_cost=round_money(bending_cost),
                    painting_cost=round_money(painting_cost),
                    total_single_part_cost_via_laser=round_money(single_laser),
                    total_single_part_cost_via_machine=round_money(single_machine),
                    total_combined_set_cost_via_laser=round_money(single_laser * qty),
                    total_combined_set_cost_via_machine=round_money(single_machine * qty),
                ),
                calculation_steps=steps,
            )
        )

    welding_length = extraction.assembly_level_fabrication.total_assembly_welding_length_mm
    welding_cost = (welding_length / 1000) * welding_labor_per_meter
    parts_laser = sum(part.calculated_costs.total_combined_set_cost_via_laser for part in costed_parts)
    parts_machine = sum(part.calculated_costs.total_combined_set_cost_via_machine for part in costed_parts)
    return StructuredCostBreakdown(
        currency=extraction.currency or "INR",
        part_name=extraction.part_name,
        per_part_breakdown=costed_parts,
        assembly_level_fabrication=AssemblyLevelFabrication(
            total_assembly_welding_length_mm=round(welding_length, 2),
            welding_labor_cost=round_money(welding_cost),
            tacking_fixed_setup_cost=round_money(tacking_fixed_setup_cost),
            grand_total_assembly_cost_via_laser=round_money(parts_laser + welding_cost + tacking_fixed_setup_cost),
            grand_total_assembly_cost_via_machine=round_money(parts_machine + welding_cost + tacking_fixed_setup_cost),
        ),
        assumptions=[
            "Gemini extracts part list, dimensions, visible operations, and welding hints; backend calculates weights/costs.",
            "Rod/profile stock uses 6000 mm. Sheet stock uses 2500 x 1250 mm rectangular nesting.",
            "If a drawing does not show a feature clearly, extracted values may be zero/null and should be reviewed.",
        ],
    )


def calculate_estimate(
    *,
    content: bytes,
    filename: str | None,
    part_name: str,
    raw_material_type: str,
    raw_material_code: str | None,
    component_materials_json: str | None,
    material_rate_per_kg: float | None,
    cutting_rate_per_meter: float,
    welding_labor_per_meter: float,
    surface_rate_per_m2: float,
    surface_type: str,
    square_tube_length_mm: float,
    square_tube_outer_mm: float,
    square_tube_thickness_mm: float,
    bottom_plate_l_mm: float,
    bottom_plate_w_mm: float,
    bottom_plate_t_mm: float,
    top_plate_l_mm: float,
    top_plate_w_mm: float,
    top_plate_t_mm: float,
    handle_od_mm: float,
    handle_thickness_mm: float,
    handle_length_mm: float,
    screw_piece_dia_mm: float,
    screw_piece_length_mm: float,
    screw_piece_qty: int,
    chair_angle_weight_per_m: float,
    chair_angle_length_mm: float,
    cutting_length_mm: float,
    cutting_surface_count: int,
    weld_length_mm: float,
    bend_count: int,
    bend_rate_per_stroke: float,
    press_machine_hits: int,
    press_machine_rate_per_hit: float,
    include_tacking_labor: bool,
    tacking_labor_fixed: float = LABOR_TACKING_FIXED,
) -> EstimateResponse:
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
    calculation_steps.extend(square_tube_steps("Square tube", square_tube_outer_mm, square_tube_thickness_mm, square_tube_length_mm, tube_weight, density, material_label))
    calculation_steps.extend(plate_steps("Bottom plate", bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm, bottom_weight, density, material_label))
    calculation_steps.extend(plate_steps("Top plate", top_plate_l_mm, top_plate_w_mm, top_plate_t_mm, top_weight, density, material_label))
    calculation_steps.extend(round_tube_steps("Handle tube", handle_od_mm, handle_thickness_mm, handle_length_mm, handle_weight, density, material_label))
    calculation_steps.extend(rod_steps("Screwing pieces", screw_piece_dia_mm, screw_piece_length_mm, screw_piece_qty, screw_weight, density, material_label))
    calculation_steps.append(CalculationStep(section="Weight", name="Chair angle / bracket weight", formula="Weight = section weight per meter x length in meter", substituted_values=f"{chair_angle_weight_per_m} kg/m x ({chair_angle_length_mm} / 1000)", result=kg(chair_angle_weight_kg)))

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
            CalculationStep(section="Process", name="Cutting cost", formula="Laser cutting cost = Total cutting length in meters x laser cut rate per meter", substituted_values=f"({cutting_length_mm} / 1000) m x {CURRENCY_UNIT} {cutting_rate_per_meter}/m across {cutting_surface_count} cut surfaces", result=money(cutting_cost)),
            CalculationStep(section="Process", name="Bending cost", formula="Bending cost = Number of bends x rate per bend", substituted_values=f"{bend_count} x {CURRENCY_UNIT} {bend_rate_per_stroke}", result=money(bending_cost)),
            CalculationStep(section="Process", name="Welding cost", formula="Welding cost = Total weld length in meters x welding rate per meter", substituted_values=f"({weld_length_mm} / 1000) m x {CURRENCY_UNIT} {welding_labor_per_meter}/m", result=money(welding_cost)),
            CalculationStep(section="Process", name="Press machine cost", formula="Press machine cost = Number of machine hits x rate per hit", substituted_values=f"{press_machine_hits} x {CURRENCY_UNIT} {press_machine_rate_per_hit}", result=money(press_machine_cost)),
            CalculationStep(section="Process", name="Tacking labor", formula="Tacking labor = fixed tacking labor when included", substituted_values=f"{'included' if include_tacking_labor else 'not included'}; fixed = {CURRENCY_UNIT} {tacking_labor_fixed}", result=money(tacking_cost)),
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
            CalculationStep(section="Surface", name="Surface area", formula="Surface area = tube outside perimeter x length + plate exposed areas + handle outside perimeter x length", substituted_values=f"(4 x {square_tube_outer_mm} x {square_tube_length_mm}) + plate areas + (pi x {handle_od_mm} x {handle_length_mm})", result=f"{round(surface_area, 4)} m2"),
            CalculationStep(section="Surface", name="Surface treatment cost", formula="Surface treatment cost = Surface area x surface treatment rate", substituted_values=f"{round(surface_area, 4)} m2 x {CURRENCY_UNIT} {surface_rate_per_m2}/m2", result=money(surface_cost)),
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
        for component_material in component_materials:
            item_name = str(component_material.get("item") or "").lower()
            if item_name and (item_name in lower_name or lower_name in item_name):
                item_material_type = normalize_material_type(str(component_material.get("material_type") or material_type), str(component_material.get("material_code") or raw_material_code or ""))
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
        material_formula = CalculationStep(section="Cost", name=f"{name} material cost", formula="Material cost = item weight x material rate", substituted_values=f"{round(weight, 3)} kg x {CURRENCY_UNIT} {item_material_rate}/kg", result=money(material_cost))
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
                formulas={"weight": weight_formula, "material": material_formula, "total": material_formula},
            )
        )

    total = total_material_cost + total_process_cost + surface_cost
    calculation_steps.extend(
        [
            CalculationStep(section="Cost", name="Material cost", formula="Material cost = Total calculated weight x material rate", substituted_values=f"{round(total_weight, 3)} kg x {CURRENCY_UNIT} {active_material_rate}/kg", result=money(total_material_cost)),
            CalculationStep(section="Stock", name="Scrap value", formula="Scrap value = Scrap weight x scrap rate", substituted_values=f"{round(total_scrap_weight, 3)} kg x {CURRENCY_UNIT} {SCRAP_RATE_PER_KG}/kg", result=money(total_scrap_value)),
            CalculationStep(section="Cost", name="Total estimated cost", formula="Total cost = Material cost + process cost + surface treatment cost", substituted_values=f"{CURRENCY_UNIT} {round_money(total_material_cost)} + {CURRENCY_UNIT} {round_money(total_process_cost)} + {CURRENCY_UNIT} {round_money(surface_cost)}", result=money(total)),
        ]
    )

    return EstimateResponse(
        part_name=part_name,
        likely_use="Vertical pillar/post used in a rail coach partition frame or similar structural partition assembly.",
        uploaded_file=filename or "uploaded diagram",
        file_size_kb=round(len(content) / 1024, 2),
        total_weight_kg=round(total_weight, 3),
        total_material_cost=round_money(total_material_cost),
        total_process_cost=round_money(total_process_cost),
        surface_treatment_cost=round_money(surface_cost),
        total_estimated_cost=round_money(total),
        material_summary=MaterialSummary(material_type=material_type, material_label=material_label, material_code=raw_material_code, density_kg_per_mm3=density, rate_per_kg=active_material_rate, default_rate_per_kg=default_rate, source="extracted/raw-material-code" if raw_material_code else "user/default"),
        stock_summary=StockSummary(total_scrap_weight_kg=round(total_scrap_weight, 3), total_scrap_value=round_money(total_scrap_value), approach="Rod/profile items use 6000 mm linear nesting. Plate/sheet items use 2500 x 1250 mm two-orientation rectangular grid nesting. Mixed-shape CNC nesting is still an estimate and should be checked by the vendor."),
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
        process_breakdown=ProcessBreakdown(cutting_cost=round_money(cutting_cost), bending_cost=round_money(bending_cost), welding_cost=round_money(welding_cost), press_machine_cost=round_money(press_machine_cost), painting_cost=round_money(surface_cost), tacking_cost=round_money(tacking_cost), cutting_length_mm=cutting_length_mm, cutting_surface_count=cutting_surface_count, bend_count=bend_count, weld_length_mm=weld_length_mm, press_machine_hits=press_machine_hits),
        calculation_steps=calculation_steps,
    )
