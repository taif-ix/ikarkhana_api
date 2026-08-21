import asyncio
import unittest

from app.api.routes.common import StructuredCostRequest
from app.api.routes.costing.routes import calculate_cost_breakdown
from app.main import app


class FormulaTrailContractTest(unittest.TestCase):
    def test_web_routes_and_structured_formula_trail(self) -> None:
        required_paths = {
            "/estimate",
            "/extract-cost-breakdown",
            "/calculate-cost-breakdown",
            "/diagram-preview",
            "/extract-dimensions",
            "/extract-references",
            "/batch-extract-references",
            "/expand-upload",
            "/batch-process/start",
        }
        self.assertFalse(required_paths - set(app.openapi()["paths"]))

        request = StructuredCostRequest.model_validate({
            "extraction": {
                "currency": "INR",
                "part_name": "Formula Test",
                "raw_material_type": "ss",
                "raw_material_code": "SS304",
                "per_part_breakdown": [{
                    "part_number": "P1",
                    "component_name": "Plate",
                    "component_type": "sheet",
                    "tube_type": "NA",
                    "material_type": "ss",
                    "material_code": "SS304",
                    "per_set_qty": 2,
                    "dimensions": {
                        "length_mm": 1000,
                        "width_or_outer_dia_mm": 500,
                        "thickness_or_wall_thickness_mm": 2,
                    },
                    "bends_per_part": 2,
                    "cutting_metrics": {
                        "laser_cutting_length_mm": 3000,
                        "press_machine_hits_count": 4,
                    },
                }],
                "assembly_level_fabrication": {
                    "total_assembly_welding_length_mm": 1000,
                },
            },
            "material_rate_per_kg": 240,
            "laser_cutting_rate_per_meter": 30,
            "press_machine_rate_per_hit": 5,
            "bend_rate_per_bend": 2,
            "welding_labor_per_meter": 400,
            "painting_rate_per_m2": 120,
            "scrap_rate_per_kg": 28,
            "tacking_fixed_setup_cost": 0,
        })

        result = asyncio.run(calculate_cost_breakdown(request))
        steps = result.per_part_breakdown[0].calculation_steps
        self.assertGreaterEqual(len(steps), 10)
        for step in steps:
            self.assertTrue(step.formula)
            self.assertTrue(step.substituted_values)
            self.assertTrue(step.result)

    def test_tube_stock_weight_trail_uses_the_displayed_values(self) -> None:
        request = StructuredCostRequest.model_validate({
            "extraction": {
                "part_name": "Pillar", "raw_material_type": "ss", "raw_material_code": "C-K201",
                "per_part_breakdown": [{
                    "part_number": "1", "component_name": "Pillar", "component_type": "tube",
                    "tube_type": "square", "material_type": "ss", "material_code": "C-K201", "per_set_qty": 1,
                    "dimensions": {"length_mm": 2581, "width_or_outer_dia_mm": 45, "secondary_width_mm": 45, "thickness_or_wall_thickness_mm": 4},
                    "bends_per_part": 0, "cutting_metrics": {"laser_cutting_length_mm": 0, "press_machine_hits_count": 0},
                }],
                "assembly_level_fabrication": {"total_assembly_welding_length_mm": 0},
            },
            "material_rate_per_kg": 240, "laser_cutting_rate_per_meter": 30,
            "press_machine_rate_per_hit": 4.75, "bend_rate_per_bend": 5,
            "welding_labor_per_meter": 400, "painting_rate_per_m2": 120,
            "scrap_rate_per_kg": 28, "tacking_fixed_setup_cost": 0,
        })
        result = asyncio.run(calculate_cost_breakdown(request))
        part = result.per_part_breakdown[0]
        steps = {step.name: step for step in part.calculation_steps}

        gross = steps["Part 1 gross RM weight"]
        self.assertEqual(gross.result, "15.547 kg")
        self.assertEqual(gross.formula, "Gross unit raw material weight (kg/part) = full stock bar weight (kg/bar) / parts cut per bar")
        for expected in ("floor(6000 / 2581) = 2", "31.094 kg / 2 = 15.547 kg", "= 838 mm"):
            self.assertIn(expected, gross.substituted_values)
        self.assertEqual(steps["Part 1 scrap waste weight"].result, "2.171 kg")
        self.assertEqual(steps["Part 1 total set gross weight"].result, "15.547 kg")
        expected_names = ("surface area", "net weight", "gross RM weight", "scrap waste weight", "total set gross weight", "laser cutting length", "press machine hits", "bend count", "material cost")
        for name in expected_names:
            self.assertTrue(any(name in step.name for step in part.calculation_steps), name)

    def test_sheet_gross_weight_uses_sheet_specific_wording(self) -> None:
        request = StructuredCostRequest.model_validate({
            "extraction": {
                "part_name": "Plate", "raw_material_type": "ss", "raw_material_code": "SS304",
                "per_part_breakdown": [{
                    "part_number": "2", "component_name": "Plate", "component_type": "sheet",
                    "tube_type": "NA", "material_type": "ss", "material_code": "SS304", "per_set_qty": 1,
                    "dimensions": {"length_mm": 150, "width_or_outer_dia_mm": 100, "thickness_or_wall_thickness_mm": 5},
                    "bends_per_part": 0, "cutting_metrics": {"laser_cutting_length_mm": 0, "press_machine_hits_count": 4},
                }],
                "assembly_level_fabrication": {"total_assembly_welding_length_mm": 0},
            },
            "material_rate_per_kg": 240, "laser_cutting_rate_per_meter": 30,
            "press_machine_rate_per_hit": 5, "bend_rate_per_bend": 5,
            "welding_labor_per_meter": 400, "painting_rate_per_m2": 120,
            "scrap_rate_per_kg": 28, "tacking_fixed_setup_cost": 0,
        })
        result = asyncio.run(calculate_cost_breakdown(request))
        gross = next(step for step in result.per_part_breakdown[0].calculation_steps if step.name == "Part 2 gross RM weight")
        self.assertEqual(gross.formula, "Gross unit raw material weight (kg/part) = full stock sheet weight (kg/sheet) / parts nested per sheet")
        self.assertIn("Full sheet weight", gross.substituted_values)


if __name__ == "__main__":
    unittest.main()
