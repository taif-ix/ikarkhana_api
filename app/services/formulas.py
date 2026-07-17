from __future__ import annotations

import math

from app.core.config import (
    CURRENCY_UNIT,
    MATERIALS,
    ROD_STOCK_LENGTH_MM,
    SCRAP_RATE_PER_KG,
    SHEET_STOCK_LENGTH_MM,
    SHEET_STOCK_WIDTH_MM,
    SS304_DENSITY_KG_PER_MM3,
)
from app.models.schemas import CalculationStep


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
        CalculationStep(section="Weight", name=f"{name} inner size", formula="Inner size = Outer size - (2 x wall thickness)", substituted_values=f"{outer_mm} - (2 x {thickness_mm})", result=f"{inner} mm"),
        CalculationStep(section="Weight", name=f"{name} steel area", formula="Steel area (mm2) = outer area (mm2) - inner hollow area (mm2)", substituted_values=f"({outer_mm} mm x {outer_mm} mm) - ({inner} mm x {inner} mm)", result=f"{round(area, 3)} mm2"),
        CalculationStep(section="Weight", name=f"{name} volume", formula="Volume (mm3) = steel area (mm2) x length (mm)", substituted_values=f"{round(area, 3)} mm2 x {length_mm} mm", result=f"{round(volume, 3)} mm3"),
        CalculationStep(section="Weight", name=f"{name} weight", formula=f"Weight (kg) = volume (mm3) x {material_label} density (kg/mm3)", substituted_values=f"{round(volume, 3)} mm3 x {density_kg_per_mm3} kg/mm3", result=kg(weight)),
    ]


def plate_steps(name: str, length_mm: float, width_mm: float, thickness_mm: float, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
    volume = length_mm * width_mm * thickness_mm
    return [
        CalculationStep(section="Weight", name=f"{name} volume", formula="Volume (mm3) = length (mm) x width (mm) x thickness (mm)", substituted_values=f"{length_mm} mm x {width_mm} mm x {thickness_mm} mm", result=f"{round(volume, 3)} mm3"),
        CalculationStep(section="Weight", name=f"{name} weight", formula=f"Weight (kg) = volume (mm3) x {material_label} density (kg/mm3)", substituted_values=f"{round(volume, 3)} mm3 x {density_kg_per_mm3} kg/mm3", result=kg(weight)),
    ]


def round_tube_steps(name: str, od_mm: float, thickness_mm: float, length_mm: float, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
    inner = max(od_mm - (2 * thickness_mm), 0)
    area = math.pi / 4 * ((od_mm * od_mm) - (inner * inner))
    return [
        CalculationStep(section="Weight", name=f"{name} inside diameter", formula="Inside diameter = Outside diameter - (2 x wall thickness)", substituted_values=f"{od_mm} - (2 x {thickness_mm})", result=f"{inner} mm"),
        CalculationStep(section="Weight", name=f"{name} steel area", formula="Steel area (mm2) = pi / 4 x (OD2 - ID2), where OD and ID are in mm", substituted_values=f"pi / 4 x ({od_mm} mm^2 - {inner} mm^2)", result=f"{round(area, 3)} mm2"),
        CalculationStep(section="Weight", name=f"{name} weight", formula=f"Weight (kg) = steel area (mm2) x length (mm) x {material_label} density (kg/mm3)", substituted_values=f"{round(area, 3)} mm2 x {length_mm} mm x {density_kg_per_mm3} kg/mm3", result=kg(weight)),
    ]


def rod_steps(name: str, diameter_mm: float, length_mm: float, quantity: int, weight: float, density_kg_per_mm3: float, material_label: str) -> list[CalculationStep]:
    area = math.pi / 4 * diameter_mm * diameter_mm
    return [
        CalculationStep(section="Weight", name=f"{name} steel area", formula="Solid round area (mm2) = pi / 4 x diameter (mm)^2", substituted_values=f"pi / 4 x {diameter_mm} mm^2", result=f"{round(area, 3)} mm2"),
        CalculationStep(section="Weight", name=f"{name} total weight", formula="Weight (kg) = area (mm2) x length (mm) x density (kg/mm3) x quantity", substituted_values=f"{round(area, 3)} mm2 x {length_mm} mm x {density_kg_per_mm3} kg/mm3 x {quantity} pcs ({material_label})", result=kg(weight)),
    ]


def find_step(steps: list[CalculationStep], name: str) -> CalculationStep:
    for step in steps:
        if step.name == name:
            return step
    return CalculationStep(section="Formula", name=name, formula="Formula not available", substituted_values="-", result="-")
