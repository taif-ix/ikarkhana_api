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


if __name__ == "__main__":
    unittest.main()
