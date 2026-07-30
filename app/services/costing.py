from typing import Dict


# Deterministic manufacturing costing helper formulas.
# =====================================================================
def premium_calculate_painting_cost(comp_type: str, length_mm: float, width_mm: float, qty: int, base_rate_sqm: float) -> float:
    if comp_type.lower() in ["accessory", "screwing_piece"]:
        return 0.00
    surface_area_sqm = (2 * length_mm * width_mm) / 1000000.0
    return round(surface_area_sqm * base_rate_sqm * qty, 2)

def premium_calculate_bending_cost(bends: int, qty: int, base_rate_stroke: float) -> float:
    return round(bends * base_rate_stroke * qty, 2)

def premium_calculate_advanced_geometries(comp_type: str, L: float, W: float, total_weld_mm: float, is_tapered: bool) -> Dict[str, float]:
    multiplier = 1.25 if is_tapered else 1.0
    cut_length_mm = 2 * (L + W) * multiplier if comp_type.lower() in ["perforated_tray", "tapered_gusset", "sheet", "handle"] else 0.0
    return {
        "cutting_length_mm": round(cut_length_mm, 2),
        "welding_length_mm": round(total_weld_mm, 2)
    }

def premium_generate_process_sequence_advisory(comp_type: str, slots: int, bends: int, is_tapered: bool) -> str:
    if comp_type.lower() == "perforated_tray":
        return f"ROUTING: [1] Laser cut perimeter blank -> [2] Turret punch matrix tool path for {slots} slots -> [3] CNC brake press forming ({bends} folds) -> [4] Passivation wash down."
    elif comp_type.lower() == "tapered_gusset" or is_tapered:
        return f"ROUTING: [1] Interlocked nesting layout configuration to protect grain structure -> [2] Precision linear laser vector profile cut -> [3] Edge debur cell."
    elif comp_type.lower() == "handle":
        return "ROUTING: [1] Flat strip laser blanking -> [2] Multi-stage radius brake forming -> [3] Fixture weld prep."
    elif comp_type.lower() == "screwing_piece":
        return "ROUTING: [1] Bar stock CNC lathe turning -> [2] Metric threading operation -> [3] De-burring and wash inspection."
    return "ROUTING: [1] Standard raw bundle stock saw feed -> [2] Edge clean cycle -> [3] Quality check queue."
