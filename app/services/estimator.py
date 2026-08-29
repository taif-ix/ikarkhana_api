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
    NestingLayoutHint,
    ProcessBreakdown,
    ReferencedDrawing,
    StockSummary,
    StructuredCostBreakdown,
    StructuredExtraction,
    WeightLedger,
)
from app.services.formulas import (
    find_step,
    fmt_number,
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


def _money_input(value: float | None, fallback: float = 0.0) -> float:
    if value is None:
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return max(numeric, 0.0)


def _geometry_cutting_metrics(part) -> tuple[float, int, list[str]]:
    """Derive process-neutral internal cut length and one-hit geometry count."""
    perimeter_mm = 0.0
    feature_count = 0
    warnings: list[str] = []
    geometry = part.flat_pattern if part.flat_pattern is not None else part

    for hole in geometry.holes:
        quantity = hole.quantity_per_part
        if quantity is None or quantity < 0:
            warnings.append("Hole has no verified quantity and was excluded from cutting metrics.")
            continue
        quantity = int(quantity)
        if hole.diameter_mm is None:
            warnings.append("Hole has no verified diameter and was excluded from laser length.")
        else:
            perimeter_mm += math.pi * max(float(hole.diameter_mm), 0.0) * quantity
        feature_count += quantity

    for slot in geometry.slots:
        quantity = slot.quantity_per_part
        if quantity is None or quantity < 0:
            warnings.append("Slot has no verified quantity and was excluded from cutting metrics.")
            continue
        quantity = int(quantity)
        if slot.length_mm is None or slot.width_mm is None:
            warnings.append("Slot has incomplete geometry and was excluded from laser length.")
        else:
            length = max(float(slot.length_mm), 0.0)
            width = max(float(slot.width_mm), 0.0)
            perimeter_mm += (2 * max(length - width, 0.0) + math.pi * width) * quantity
        feature_count += quantity

    for collection_name in ("notches", "cutouts"):
        for item in getattr(geometry, collection_name):
            quantity = item.quantity_per_part
            if quantity is None or quantity < 0 or item.length_mm is None or item.width_mm is None:
                warnings.append(f"{collection_name[:-1].title()} has incomplete geometry and was excluded from cutting metrics.")
                continue
            perimeter_mm += 2 * (max(float(item.length_mm), 0.0) + max(float(item.width_mm), 0.0)) * int(quantity)
            feature_count += int(quantity)
    return perimeter_mm, feature_count, warnings


def _polygon_perimeter_mm(part) -> float | None:
    contour = part.flat_pattern.outer_contour if part.flat_pattern else None
    if contour is None or len(contour.points_mm) < 3:
        return None
    points = contour.points_mm
    return sum(math.hypot(points[(i + 1) % len(points)].x - point.x, points[(i + 1) % len(points)].y - point.y) for i, point in enumerate(points))


def _outer_profile_cut_length_mm(component_type: str, tube_type: str, length_mm: float, width_mm: float, outer_diameter_mm: float) -> float:
    component = (component_type or "").lower()
    if component in {"sheet", "plate"} and length_mm > 0 and width_mm > 0:
        return 2 * (length_mm + width_mm)
    if component in {"rod", "accessory"} and outer_diameter_mm > 0:
        return math.pi * outer_diameter_mm
    # Tube/profile stock cut paths depend on the selected machine and tooling;
    # do not invent them from an orthographic envelope.
    return 0.0


def _is_round_profile_text(text: str) -> bool:
    normalized = (text or "").lower()
    return any(token in normalized for token in ("round", "dia", "diameter", "od", "ø", "⌀"))


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
    if component == "tube" and _is_round_profile_text(tube):
        area = math.pi / 4 * (width_or_dia_mm**2 - max(width_or_dia_mm - (2 * thickness_mm), 0) ** 2)
        weight = area * length_mm * density
        inner_dia = max(width_or_dia_mm - (2 * thickness_mm), 0)
        surface = tube_surface_area_m2(math.pi * width_or_dia_mm + math.pi * inner_dia, length_mm)
        return surface, weight
    if component == "tube":
        outer_b = secondary_width_mm or width_or_dia_mm
        inner_a = max(width_or_dia_mm - (2 * thickness_mm), 0)
        inner_b = max(outer_b - (2 * thickness_mm), 0)
        area = (width_or_dia_mm * outer_b) - (inner_a * inner_b)
        weight = area * length_mm * density
        surface = tube_surface_area_m2(2 * (width_or_dia_mm + outer_b) + 2 * (inner_a + inner_b), length_mm)
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


def _surface_area_step_text(
    component_type: str,
    tube_type: str,
    length_mm: float,
    width_or_dia_mm: float,
    secondary_width_mm: float | None,
    thickness_mm: float,
) -> tuple[str, str]:
    component = component_type.lower()
    tube = tube_type.lower()
    if component == "tube" and _is_round_profile_text(tube):
        perimeter = math.pi * width_or_dia_mm
        inner_dia = max(width_or_dia_mm - (2 * thickness_mm), 0)
        inner_perimeter = math.pi * inner_dia
        total_perimeter = perimeter + inner_perimeter
        return (
            "Hollow round tube painted area (m2) = (outside circumference + inside circumference) x length / 1,000,000 mm2 per m2",
            f"(pi x {fmt_number(width_or_dia_mm)} mm + pi x {fmt_number(inner_dia)} mm) = {fmt_number(total_perimeter)} mm perimeter; {fmt_number(total_perimeter)} mm x {fmt_number(length_mm)} mm = {fmt_number(total_perimeter * length_mm)} mm2; / 1,000,000 = {fmt_number(total_perimeter * length_mm / 1_000_000, 4)} m2",
        )
    if component == "tube":
        outer_b = secondary_width_mm or width_or_dia_mm
        perimeter = 2 * (width_or_dia_mm + outer_b)
        inner_a = max(width_or_dia_mm - (2 * thickness_mm), 0)
        inner_b = max(outer_b - (2 * thickness_mm), 0)
        inner_perimeter = 2 * (inner_a + inner_b)
        total_perimeter = perimeter + inner_perimeter
        return (
            "Hollow square/rectangular tube painted area (m2) = (outside perimeter + inside perimeter) x length / 1,000,000 mm2 per m2",
            f"outside perimeter = 2 x ({fmt_number(width_or_dia_mm)} mm + {fmt_number(outer_b)} mm) = {fmt_number(perimeter)} mm; inside perimeter = 2 x ({fmt_number(inner_a)} mm + {fmt_number(inner_b)} mm) = {fmt_number(inner_perimeter)} mm; total {fmt_number(total_perimeter)} mm x {fmt_number(length_mm)} mm = {fmt_number(total_perimeter * length_mm)} mm2; / 1,000,000 = {fmt_number(total_perimeter * length_mm / 1_000_000, 4)} m2",
        )
    if component in {"sheet", "plate"}:
        surface = 2 * ((length_mm * width_or_dia_mm) + (length_mm * thickness_mm) + (width_or_dia_mm * thickness_mm)) / 1_000_000
        return (
            "Plate surface area (m2) = 2 x ((L x W) + (L x T) + (W x T)) in mm2 / 1,000,000 mm2 per m2",
            f"2 x (({fmt_number(length_mm)} mm x {fmt_number(width_or_dia_mm)} mm) + ({fmt_number(length_mm)} mm x {fmt_number(thickness_mm)} mm) + ({fmt_number(width_or_dia_mm)} mm x {fmt_number(thickness_mm)} mm)) = {fmt_number(surface * 1_000_000)} mm2; / 1,000,000 = {fmt_number(surface, 4)} m2",
        )
    if component in {"rod", "accessory"}:
        radius = width_or_dia_mm / 2
        surface = ((2 * math.pi * radius * length_mm) + (2 * math.pi * radius * radius)) / 1_000_000
        return (
            "Solid round rod surface area (m2) = curved area (mm2) + two end faces (mm2), then / 1,000,000 mm2 per m2",
            f"((2 x pi x {fmt_number(radius)} mm x {fmt_number(length_mm)} mm) + (2 x pi x {fmt_number(radius)} mm x {fmt_number(radius)} mm)) = {fmt_number(surface * 1_000_000)} mm2; / 1,000,000 = {fmt_number(surface, 4)} m2",
        )
    surface = 2 * ((length_mm * width_or_dia_mm) + (length_mm * thickness_mm) + (width_or_dia_mm * thickness_mm)) / 1_000_000
    return (
        "Surface area (m2) = 2 x ((L x W) + (L x T) + (W x T)) in mm2 / 1,000,000 mm2 per m2",
        f"2 x (({fmt_number(length_mm)} mm x {fmt_number(width_or_dia_mm)} mm) + ({fmt_number(length_mm)} mm x {fmt_number(thickness_mm)} mm) + ({fmt_number(width_or_dia_mm)} mm x {fmt_number(thickness_mm)} mm)) = {fmt_number(surface * 1_000_000)} mm2; / 1,000,000 = {fmt_number(surface, 4)} m2",
    )


def _weight_step_text(
    component_type: str,
    tube_type: str,
    length_mm: float,
    width_or_dia_mm: float,
    secondary_width_mm: float | None,
    thickness_mm: float,
    density: float,
) -> tuple[str, str]:
    component = component_type.lower()
    tube = tube_type.lower()
    if component == "tube" and _is_round_profile_text(tube):
        inner_dia = max(width_or_dia_mm - (2 * thickness_mm), 0)
        steel_area = math.pi / 4 * (width_or_dia_mm**2 - inner_dia**2)
        volume = steel_area * length_mm
        return (
            "Net weight (kg) = steel cross-section area (mm2) x length (mm) x material density (kg/mm3)",
            f"area = pi/4 x ({fmt_number(width_or_dia_mm)}^2 - {fmt_number(inner_dia)}^2) = {fmt_number(steel_area)} mm2; volume = {fmt_number(steel_area)} mm2 x {fmt_number(length_mm)} mm = {fmt_number(volume)} mm3; weight = {fmt_number(volume)} mm3 x {density} kg/mm3",
        )
    if component == "tube":
        outer_b = secondary_width_mm or width_or_dia_mm
        inner_a = max(width_or_dia_mm - (2 * thickness_mm), 0)
        inner_b = max(outer_b - (2 * thickness_mm), 0)
        steel_area = (width_or_dia_mm * outer_b) - (inner_a * inner_b)
        volume = steel_area * length_mm
        return (
            "Net weight (kg) = hollow tube steel area (mm2) x length (mm) x material density (kg/mm3)",
            f"area = ({fmt_number(width_or_dia_mm)} mm x {fmt_number(outer_b)} mm) - ({fmt_number(inner_a)} mm x {fmt_number(inner_b)} mm) = {fmt_number(steel_area)} mm2; volume = {fmt_number(steel_area)} mm2 x {fmt_number(length_mm)} mm = {fmt_number(volume)} mm3; weight = {fmt_number(volume)} mm3 x {density} kg/mm3",
        )
    if component in {"sheet", "plate"}:
        volume = length_mm * width_or_dia_mm * thickness_mm
        return (
            "Net weight (kg) = volume (mm3) x material density (kg/mm3)",
            f"volume = {fmt_number(length_mm)} mm x {fmt_number(width_or_dia_mm)} mm x {fmt_number(thickness_mm)} mm = {fmt_number(volume)} mm3; weight = {fmt_number(volume)} mm3 x {density} kg/mm3",
        )
    if component in {"rod", "accessory"}:
        steel_area = math.pi / 4 * width_or_dia_mm * width_or_dia_mm
        volume = steel_area * length_mm
        return (
            "Net weight (kg) = solid round area (mm2) x length (mm) x material density (kg/mm3)",
            f"area = pi/4 x {fmt_number(width_or_dia_mm)}^2 = {fmt_number(steel_area)} mm2; volume = {fmt_number(steel_area)} mm2 x {fmt_number(length_mm)} mm = {fmt_number(volume)} mm3; weight = {fmt_number(volume)} mm3 x {density} kg/mm3",
        )
    volume = length_mm * width_or_dia_mm * thickness_mm
    return (
        "Net weight (kg) = volume (mm3) x material density (kg/mm3)",
        f"volume = {fmt_number(length_mm)} mm x {fmt_number(width_or_dia_mm)} mm x {fmt_number(thickness_mm)} mm = {fmt_number(volume)} mm3; weight = {fmt_number(volume)} mm3 x {density} kg/mm3",
    )


def _stock_for_part(component_type: str, net_weight: float, length_mm: float, width_mm: float, thickness_mm: float, density: float, qty: int) -> tuple[float, float, str, dict]:
    component = component_type.lower()
    if component in {"tube", "rod", "accessory"}:
        stock = rod_stock_summary(net_weight, length_mm, qty)
    else:
        stock = sheet_nesting_summary(length_mm, width_mm, thickness_mm, density, qty)
    parts_per_stock = max(int(stock["parts_per_stock"]), 1)
    gross_unit = float(stock["stock_weight_kg"]) / parts_per_stock
    scrap_unit = max(gross_unit - net_weight, 0)
    return gross_unit, scrap_unit, str(stock["approach"]), stock


def calculate_structured_cost_breakdown(
    extraction: StructuredExtraction,
    *,
    material_rate_per_kg: float | None,
    laser_cutting_rate_per_meter: float,
    press_machine_rate_per_hit: float,
    bend_rate_per_bend: float,
    welding_labor_per_meter: float,
    painting_rate_per_m2: float,
    scrap_rate_per_kg: float,
    tacking_fixed_setup_cost: float,
) -> StructuredCostBreakdown:
    material_type = normalize_material_type(extraction.raw_material_type, extraction.raw_material_code)
    default_details = MATERIALS[material_type]
    default_density = float(default_details["density"])
    default_rate = _money_input(material_rate_per_kg, float(default_details["default_rate"]))
    laser_cutting_rate_per_meter = _money_input(laser_cutting_rate_per_meter)
    press_machine_rate_per_hit = _money_input(press_machine_rate_per_hit)
    bend_rate_per_bend = _money_input(bend_rate_per_bend)
    welding_labor_per_meter = _money_input(welding_labor_per_meter)
    painting_rate_per_m2 = _money_input(painting_rate_per_m2)
    scrap_rate_per_kg = _money_input(scrap_rate_per_kg)
    tacking_fixed_setup_cost = _money_input(tacking_fixed_setup_cost)
    costed_parts: list[CostedPartBreakdown] = []

    for index, part in enumerate(extraction.per_part_breakdown, start=1):
        profile_text = part.profile.shape if part.profile else "NA"
        dims = part.dimensions
        length = max(float(dims.length_mm or 0), 0.0)
        width = max(float(dims.width_mm or 0), 0.0)
        height = dims.height_mm
        outer_diameter = max(float(dims.outer_diameter_mm or 0), 0.0)
        width_or_dia = outer_diameter or width or max(float(height or 0), 0.0)
        secondary_width = height
        if secondary_width is not None:
            secondary_width = max(float(secondary_width), 0.0)
        thickness = max(float(dims.thickness_mm or 0), 0.0)
        qty = max(int(part.per_set_qty or 1), 1)
        item_material_type = normalize_material_type(part.material_type or material_type, part.material_code or extraction.raw_material_code)
        item_details = MATERIALS[item_material_type]
        density = float(item_details["density"])
        rate = default_rate if item_material_type == material_type else float(item_details["default_rate"])

        surface_area, net_weight = _part_area_and_weight(part.component_type, profile_text, length, width_or_dia, secondary_width, thickness, density)
        surface_formula, surface_values = _surface_area_step_text(part.component_type, profile_text, length, width_or_dia, secondary_width, thickness)
        weight_formula, weight_values = _weight_step_text(part.component_type, profile_text, length, width_or_dia, secondary_width, thickness, density)
        gross_weight, scrap_weight, stock_approach, stock = _stock_for_part(part.component_type, net_weight, length, width_or_dia, thickness, density, qty)
        outer_profile_length_mm = _polygon_perimeter_mm(part) or _outer_profile_cut_length_mm(
            part.component_type,
            profile_text,
            length,
            width,
            outer_diameter,
        )
        feature_length_mm, feature_hits, feature_warnings = _geometry_cutting_metrics(part)
        laser_length_mm = outer_profile_length_mm + feature_length_mm
        press_hits = feature_hits
        bends = max(int(part.bends_per_part or 0), 0)
        laser_cutting_cost = (laser_length_mm / 1000) * laser_cutting_rate_per_meter
        machine_punching_cost = press_hits * press_machine_rate_per_hit
        bending_cost = bends * bend_rate_per_bend
        painting_cost = surface_area * painting_rate_per_m2
        gross_material_cost = gross_weight * rate
        scrap_value = scrap_weight * scrap_rate_per_kg
        material_cost = max(gross_material_cost - scrap_value, 0)
        single_laser = material_cost + laser_cutting_cost + bending_cost + painting_cost
        single_machine = material_cost + machine_punching_cost + bending_cost + painting_cost
        parts_per_stock = max(int(stock["parts_per_stock"]), 1)
        stock_weight = float(stock["stock_weight_kg"])
        if part.component_type.lower() in {"tube", "rod", "accessory"}:
            gross_weight_formula = "Gross unit raw material weight (kg/part) = full stock bar weight (kg/bar) / parts cut per bar"
            stock_values = (
                f"Stock length = 6000 mm; Part length = {fmt_number(length)} mm; "
                f"Parts per stock = floor(6000 / {fmt_number(length)}) = {parts_per_stock}; "
                f"Full stock weight = {fmt_number(net_weight)} kg x (6000 / {fmt_number(length)}) = {fmt_number(stock_weight)} kg; "
                f"Gross unit weight = {fmt_number(stock_weight)} kg / {parts_per_stock} = {fmt_number(gross_weight)} kg; "
                f"Leftover per full stock = 6000 - ({parts_per_stock} x {fmt_number(length)}) = {fmt_number(float(stock.get('leftover_per_stock_mm', 0)))} mm"
            )
        else:
            gross_weight_formula = "Gross unit raw material weight (kg/part) = full stock sheet weight (kg/sheet) / parts nested per sheet"
            stock_values = (
                f"{stock_approach}; Full sheet weight = 2500 mm x 1250 mm x {fmt_number(thickness)} mm x {density} kg/mm3 = {fmt_number(stock_weight)} kg; "
                f"Gross unit weight = {fmt_number(stock_weight)} kg / {parts_per_stock} = {fmt_number(gross_weight)} kg"
            )

        steps = [
            CalculationStep(
                section="Area",
                name=f"Part {part.part_number} surface area",
                formula=surface_formula,
                substituted_values=surface_values,
                result=f"{round(surface_area, 4)} m2",
            ),
            CalculationStep(
                section="Weight",
                name=f"Part {part.part_number} net weight",
                formula=weight_formula,
                substituted_values=f"{weight_values}; Net finished weight = {fmt_number(net_weight)} kg",
                result=kg(net_weight),
            ),
            CalculationStep(
                section="Stock",
                name=f"Part {part.part_number} gross RM weight",
                formula=gross_weight_formula,
                substituted_values=stock_values,
                result=kg(gross_weight),
            ),
            CalculationStep(
                section="Stock",
                name=f"Part {part.part_number} scrap waste weight",
                formula="Scrap / waste weight (kg/part) = gross unit raw material weight - net finished weight",
                substituted_values=f"{fmt_number(gross_weight)} kg - {fmt_number(net_weight)} kg = {fmt_number(scrap_weight)} kg",
                result=kg(scrap_weight),
            ),
            CalculationStep(
                section="Stock",
                name=f"Part {part.part_number} total set gross weight",
                formula="Total set gross weight (kg) = gross unit raw material weight (kg/part) x quantity",
                substituted_values=f"{fmt_number(gross_weight)} kg/part x {qty} parts = {fmt_number(gross_weight * qty)} kg",
                result=kg(gross_weight * qty),
            ),
            CalculationStep(
                section="Cost",
                name=f"Part {part.part_number} material cost",
                formula="Net material cost (INR) = gross raw material cost (INR) - scrap resale value (INR)",
                substituted_values=f"({fmt_number(gross_weight)} kg x {CURRENCY_UNIT} {fmt_number(rate, 2)}/kg) - ({fmt_number(scrap_weight)} kg scrap x {CURRENCY_UNIT} {fmt_number(scrap_rate_per_kg, 2)}/kg) = {money(gross_material_cost)} - {money(scrap_value)}",
                result=money(material_cost),
            ),
            CalculationStep(
                section="Stock",
                name=f"Part {part.part_number} scrap resale value",
                formula="Scrap resale value (INR) = scrap weight (kg) x scrap rate (INR/kg)",
                substituted_values=f"{fmt_number(scrap_weight)} kg x {CURRENCY_UNIT} {fmt_number(scrap_rate_per_kg, 2)}/kg = {money(scrap_value)}",
                result=money(scrap_value),
            ),
            CalculationStep(
                section="Input",
                name=f"Part {part.part_number} laser cutting length",
                formula="Laser cutting length (mm) = extracted drawing cut-path length",
                substituted_values=f"Extracted cut-path length = {fmt_number(laser_length_mm)} mm",
                result=f"{fmt_number(laser_length_mm)} mm",
            ),
            CalculationStep(
                section="Process",
                name=f"Part {part.part_number} laser cutting cost",
                formula="Laser cutting cost (INR) = laser cutting length (m) x laser cut rate (INR/m)",
                substituted_values=f"{fmt_number(laser_length_mm)} mm / 1000 = {fmt_number(laser_length_mm / 1000)} m; {fmt_number(laser_length_mm / 1000)} m x {CURRENCY_UNIT} {fmt_number(laser_cutting_rate_per_meter, 2)}/m = {money(laser_cutting_cost)}",
                result=money(laser_cutting_cost),
            ),
            CalculationStep(
                section="Input",
                name=f"Part {part.part_number} press machine hits",
                formula="Press machine hits = extracted drawing punch / press feature count",
                substituted_values=f"Extracted press feature count = {press_hits} hits",
                result=f"{press_hits} hits",
            ),
            CalculationStep(
                section="Process",
                name=f"Part {part.part_number} press cutting cost",
                formula="Press / punching cost (INR) = press hit count (hits) x press cut rate (INR/hit)",
                substituted_values=f"{press_hits} hits x {CURRENCY_UNIT} {fmt_number(press_machine_rate_per_hit, 2)}/hit = {money(machine_punching_cost)}",
                result=money(machine_punching_cost),
            ),
            CalculationStep(
                section="Input",
                name=f"Part {part.part_number} bend count",
                formula="Bend count = extracted drawing bend count per part",
                substituted_values=f"Extracted bend count = {bends} bends",
                result=f"{bends} bends",
            ),
            CalculationStep(
                section="Process",
                name=f"Part {part.part_number} bending cost",
                formula="Bending cost (INR) = bend count (bends) x bend rate (INR/bend)",
                substituted_values=f"{bends} bends x {CURRENCY_UNIT} {fmt_number(bend_rate_per_bend, 2)}/bend = {money(bending_cost)}",
                result=money(bending_cost),
            ),
            CalculationStep(
                section="Surface",
                name=f"Part {part.part_number} painting cost",
                formula="Painting cost (INR) = surface area (m2) x painting rate (INR/m2)",
                substituted_values=f"{fmt_number(surface_area, 4)} m2 x {CURRENCY_UNIT} {fmt_number(painting_rate_per_m2, 2)}/m2 = {money(painting_cost)}",
                result=money(painting_cost),
            ),
            CalculationStep(
                section="Cost",
                name=f"Part {part.part_number} total via laser",
                formula="Single part laser route = material cost + laser cutting cost + bending cost + painting cost",
                substituted_values=f"{money(material_cost)} + {money(laser_cutting_cost)} + {money(bending_cost)} + {money(painting_cost)}",
                result=money(single_laser),
            ),
            CalculationStep(
                section="Cost",
                name=f"Part {part.part_number} total via machine",
                formula="Single part machine route = material cost + press cutting cost + bending cost + painting cost",
                substituted_values=f"{money(material_cost)} + {money(machine_punching_cost)} + {money(bending_cost)} + {money(painting_cost)}",
                result=money(single_machine),
            ),
        ]

        part_payload = part.model_dump()
        part_payload["part_number"] = part.part_number or str(index)
        part_payload["material_type"] = item_material_type
        part_payload["material_code"] = part.material_code or extraction.raw_material_code
        part_payload["bends_per_part"] = bends
        part_payload["cutting_metrics"] = {
            "laser_cutting_length_mm": laser_length_mm,
            "press_machine_hits_count": press_hits,
            "outer_profile_cut_length_mm": outer_profile_length_mm,
            "internal_feature_cut_length_mm": feature_length_mm,
            "internal_feature_count": feature_hits,
        }
        part_payload["tube_type"] = profile_text
        part_payload["notes"] = feature_warnings
        part_payload["nesting_layout_hint"] = NestingLayoutHint(
            nesting_strategy=stock_approach,
            recommended_grain_or_cut_direction=(part.nesting_constraints.grain_direction if part.nesting_constraints else None) or "NA",
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

    welding_length = max(float(extraction.assembly_fabrication.welding_length_mm or 0), 0.0)
    referenced_drawings = [
        ReferencedDrawing(
            drawing_number=part.referenced_drawing_number,
            referenced_by_part_number=part.part_number,
            referenced_by_component=part.component_name,
        )
        for part in extraction.per_part_breakdown
        if part.referenced_drawing_number
    ]
    welding_cost = (welding_length / 1000) * welding_labor_per_meter
    parts_laser = sum(part.calculated_costs.total_combined_set_cost_via_laser for part in costed_parts)
    parts_machine = sum(part.calculated_costs.total_combined_set_cost_via_machine for part in costed_parts)
    return StructuredCostBreakdown(
        currency="INR",
        part_name=None,
        raw_material_type=extraction.raw_material_type,
        raw_material_code=extraction.raw_material_code,
        referenced_drawings=referenced_drawings,
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
            f"Scrap resale is deducted from gross raw material cost at {CURRENCY_UNIT} {scrap_rate_per_kg}/kg.",
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
    scrap_rate_per_kg: float,
    include_tacking_labor: bool,
    tacking_labor_fixed: float = LABOR_TACKING_FIXED,
) -> EstimateResponse:
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    material_type = normalize_material_type(raw_material_type, raw_material_code)
    details = MATERIALS[material_type]
    material_label = str(details["label"])
    density = float(details["density"])
    default_rate = float(details["default_rate"])
    active_material_rate = _money_input(material_rate_per_kg, default_rate)
    cutting_rate_per_meter = _money_input(cutting_rate_per_meter)
    welding_labor_per_meter = _money_input(welding_labor_per_meter)
    surface_rate_per_m2 = _money_input(surface_rate_per_m2)
    bend_rate_per_stroke = _money_input(bend_rate_per_stroke)
    press_machine_rate_per_hit = _money_input(press_machine_rate_per_hit)
    scrap_rate_per_kg = _money_input(scrap_rate_per_kg)
    tacking_labor_fixed = _money_input(tacking_labor_fixed)
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
    calculation_steps.append(CalculationStep(section="Weight", name="Chair angle / bracket weight", formula="Weight = section weight per meter x length in meter", substituted_values=f"{fmt_number(chair_angle_weight_per_m)} kg/m x ({fmt_number(chair_angle_length_mm)} / 1000)", result=kg(chair_angle_weight_kg)))

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
            CalculationStep(section="Process", name="Cutting cost", formula="Laser cutting cost = Total cutting length in meters x laser cut rate per meter", substituted_values=f"({fmt_number(cutting_length_mm)} / 1000) m x {CURRENCY_UNIT} {fmt_number(cutting_rate_per_meter, 2)}/m across {cutting_surface_count} cut surfaces", result=money(cutting_cost)),
            CalculationStep(section="Process", name="Bending cost", formula="Bending cost = Number of bends x rate per bend", substituted_values=f"{bend_count} x {CURRENCY_UNIT} {fmt_number(bend_rate_per_stroke, 2)}", result=money(bending_cost)),
            CalculationStep(section="Process", name="Welding cost", formula="Welding cost = Total weld length in meters x welding rate per meter", substituted_values=f"({fmt_number(weld_length_mm)} / 1000) m x {CURRENCY_UNIT} {fmt_number(welding_labor_per_meter, 2)}/m", result=money(welding_cost)),
            CalculationStep(section="Process", name="Press machine cost", formula="Press machine cost = Number of machine hits x rate per hit", substituted_values=f"{press_machine_hits} x {CURRENCY_UNIT} {fmt_number(press_machine_rate_per_hit, 2)}", result=money(press_machine_cost)),
            CalculationStep(section="Process", name="Tacking labor", formula="Tacking labor = fixed tacking labor when included", substituted_values=f"{'included' if include_tacking_labor else 'not included'}; fixed = {CURRENCY_UNIT} {tacking_labor_fixed}", result=money(tacking_cost)),
        ]
    )

    square_tube_inner_mm = max(square_tube_outer_mm - (2 * square_tube_thickness_mm), 0)
    handle_inner_od_mm = max(handle_od_mm - (2 * handle_thickness_mm), 0)
    surface_area = (
        tube_surface_area_m2((4 * square_tube_outer_mm) + (4 * square_tube_inner_mm), square_tube_length_mm)
        + plate_surface_area_m2(bottom_plate_l_mm, bottom_plate_w_mm, bottom_plate_t_mm)
        + plate_surface_area_m2(top_plate_l_mm, top_plate_w_mm, top_plate_t_mm)
        + tube_surface_area_m2((math.pi * handle_od_mm) + (math.pi * handle_inner_od_mm), handle_length_mm)
    )
    surface_cost = 0.0 if surface_type == "none" else surface_area * surface_rate_per_m2
    calculation_steps.extend(
        [
            CalculationStep(section="Surface", name="Surface area", formula="Surface area = tube inner+outer perimeter x length + plate exposed areas + handle inner+outer perimeter x length", substituted_values=f"((4 x {fmt_number(square_tube_outer_mm)} + 4 x {fmt_number(square_tube_inner_mm)}) x {fmt_number(square_tube_length_mm)}) + plate areas + ((pi x {fmt_number(handle_od_mm)} + pi x {fmt_number(handle_inner_od_mm)}) x {fmt_number(handle_length_mm)}); all mm2 divided by 1,000,000 to get m2", result=f"{fmt_number(surface_area, 4)} m2"),
            CalculationStep(section="Surface", name="Surface treatment cost", formula="Surface treatment cost = Surface area x surface treatment rate", substituted_values=f"{fmt_number(surface_area, 4)} m2 x {CURRENCY_UNIT} {fmt_number(surface_rate_per_m2, 2)}/m2", result=money(surface_cost)),
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
        total_weight += weight
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

        parts_per_stock = max(int(stock_info["parts_per_stock"]), 1) if stock_info else 1
        full_stock_scrap_weight = float(stock_info["scrap_weight_kg"]) if stock_info else 0.0
        scrap_weight = (full_stock_scrap_weight / parts_per_stock) * quantity if stock_info else 0.0
        scrap_value = scrap_weight * scrap_rate_per_kg
        total_scrap_weight += scrap_weight
        total_scrap_value += scrap_value
        stock_weight = float(stock_info["stock_weight_kg"]) if stock_info else 0.0
        allocated_gross_cost = ((stock_weight * item_material_rate) / parts_per_stock) * quantity if stock_info else weight * item_material_rate
        material_cost = max(allocated_gross_cost - scrap_value, 0.0)
        total_material_cost += material_cost
        weight_formula = find_step(calculation_steps, weight_step_name)
        material_formula = CalculationStep(section="Cost", name=f"{name} material cost", formula="Net material cost = allocated gross stock cost - allocated scrap resale value", substituted_values=f"{money(allocated_gross_cost)} - {money(scrap_value)}; gross uses stock allocation at {CURRENCY_UNIT} {item_material_rate}/kg", result=money(material_cost))
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
                parts_per_stock=parts_per_stock if stock_info else None,
                stock_weight_kg=round(stock_weight, 3) if stock_info else None,
                gross_stock_cost=round_money(stock_weight * item_material_rate) if stock_info else None,
                scrap_weight_kg=round(scrap_weight, 3),
                scrap_value=round_money(scrap_value),
                net_stock_cost_per_part=round_money(max(((stock_weight * item_material_rate) - (full_stock_scrap_weight * scrap_rate_per_kg)) / parts_per_stock, 0.0)) if stock_info else None,
                nesting_approach=str(stock_info["approach"]) if stock_info else None,
                formulas={"weight": weight_formula, "material": material_formula, "total": material_formula},
            )
        )

    total = total_material_cost + total_process_cost + surface_cost
    calculation_steps.extend(
        [
            CalculationStep(section="Cost", name="Material cost", formula="Net material cost = sum of allocated stock material costs after scrap resale deduction", substituted_values=f"sum(line item allocated gross stock costs) - {money(total_scrap_value)} scrap resale", result=money(total_material_cost)),
            CalculationStep(section="Stock", name="Scrap value", formula="Scrap value = Scrap weight x scrap rate", substituted_values=f"{fmt_number(total_scrap_weight)} kg x {CURRENCY_UNIT} {fmt_number(scrap_rate_per_kg, 2)}/kg", result=money(total_scrap_value)),
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
        stock_summary=StockSummary(scrap_rate_per_kg=scrap_rate_per_kg, total_scrap_weight_kg=round(total_scrap_weight, 3), total_scrap_value=round_money(total_scrap_value), approach="Rod/profile items use 6000 mm linear nesting. Plate/sheet items use 2500 x 1250 mm two-orientation rectangular grid nesting. Scrap/offcut is allocated per possible part from stock, not shown as the entire stock sheet leftover."),
        assumptions=[
            f"Dimension extraction is handled by Gemini API with {model}; this costing step uses the submitted field values.",
            f"Material is treated as {material_label} ({raw_material_code or material_type}) at {CURRENCY_UNIT} {active_material_rate}/kg; this can be overridden from UI.",
            f"Default material rates: MS {RATE_MS_PER_KG}/kg, SS {RATE_SS_PER_KG}/kg, aluminium {RATE_ALUMINIUM_PER_KG}/kg, copper {RATE_COPPER_PER_KG}/kg.",
            f"Default stock sizes: rod/profile {ROD_STOCK_LENGTH_MM:.0f} mm and sheet/plate {SHEET_STOCK_LENGTH_MM:.0f} x {SHEET_STOCK_WIDTH_MM:.0f} mm.",
            f"Scrap/offcut value is estimated at {CURRENCY_UNIT} {scrap_rate_per_kg}/kg.",
            "Tax, packaging, transport, and supplier MOQ are not included.",
            "Chair angle weight uses kg/m x length because the detailed LS10269 geometry is not present in the upload.",
            f"Process cost includes laser cutting ({cutting_length_mm} mm across {cutting_surface_count} surfaces), bending ({bend_count} bends), welding ({weld_length_mm} mm), press hits ({press_machine_hits}), painting, and optional tacking labor.",
        ],
        items=line_items,
        process_breakdown=ProcessBreakdown(cutting_cost=round_money(cutting_cost), bending_cost=round_money(bending_cost), welding_cost=round_money(welding_cost), press_machine_cost=round_money(press_machine_cost), painting_cost=round_money(surface_cost), tacking_cost=round_money(tacking_cost), cutting_length_mm=cutting_length_mm, cutting_surface_count=cutting_surface_count, bend_count=bend_count, weld_length_mm=weld_length_mm, press_machine_hits=press_machine_hits),
        calculation_steps=calculation_steps,
    )
