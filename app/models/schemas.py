from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import (
    RATE_SS_PER_KG,
    ROD_STOCK_LENGTH_MM,
    SCRAP_RATE_PER_KG,
    SHEET_STOCK_LENGTH_MM,
    SHEET_STOCK_WIDTH_MM,
)


class CalculationStep(BaseModel):
    section: str
    name: str
    formula: str
    substituted_values: str
    result: str


class LineItem(BaseModel):
    name: str
    quantity: int
    weight_kg: float
    material_cost: float
    process_cost: float
    total_cost: float
    material_type: str = "ss"
    material_label: str = "Stainless Steel"
    material_rate_per_kg: float = RATE_SS_PER_KG
    stock_form: str | None = None
    stock_size: str | None = None
    parts_per_stock: int | None = None
    stock_weight_kg: float | None = None
    gross_stock_cost: float | None = None
    scrap_weight_kg: float | None = None
    scrap_value: float | None = None
    net_stock_cost_per_part: float | None = None
    nesting_approach: str | None = None
    formulas: dict[str, CalculationStep] = Field(default_factory=dict)


class ProcessBreakdown(BaseModel):
    cutting_cost: float
    bending_cost: float
    welding_cost: float
    press_machine_cost: float
    painting_cost: float = 0
    tacking_cost: float
    cutting_length_mm: float
    cutting_surface_count: int = 0
    bend_count: int
    weld_length_mm: float
    press_machine_hits: int


class MaterialSummary(BaseModel):
    material_type: str
    material_label: str
    material_code: str | None
    density_kg_per_mm3: float
    rate_per_kg: float
    default_rate_per_kg: float
    source: str


class StockSummary(BaseModel):
    rod_stock_length_mm: float = ROD_STOCK_LENGTH_MM
    sheet_stock_length_mm: float = SHEET_STOCK_LENGTH_MM
    sheet_stock_width_mm: float = SHEET_STOCK_WIDTH_MM
    scrap_rate_per_kg: float = SCRAP_RATE_PER_KG
    total_scrap_weight_kg: float
    total_scrap_value: float
    approach: str


class GeminiConfig(BaseModel):
    provider: Literal["gemini_api", "vertex_ai"]
    api_key_configured: bool
    project_configured: bool
    project: str | None
    location: str
    model: str
    google_genai_installed: bool
    pillow_installed: bool


class ExtractedDimensions(BaseModel):
    part_name: str = "Pillar Assembly"
    raw_material_type: str | None = None
    raw_material_code: str | None = None
    component_materials: list[dict[str, str | float | int | None]] = Field(default_factory=list)
    main_material_form: str | None = None
    main_profile_shape: str | None = None
    main_profile_is_hollow: bool | None = None
    main_profile_length_mm: float | None = None
    main_profile_outer_a_mm: float | None = None
    main_profile_outer_b_mm: float | None = None
    main_profile_diameter_mm: float | None = None
    main_profile_thickness_mm: float | None = None
    square_tube_length_mm: float | None = None
    square_tube_outer_mm: float | None = None
    square_tube_thickness_mm: float | None = None
    bottom_plate_l_mm: float | None = None
    bottom_plate_w_mm: float | None = None
    bottom_plate_t_mm: float | None = None
    top_plate_l_mm: float | None = None
    top_plate_w_mm: float | None = None
    top_plate_t_mm: float | None = None
    handle_od_mm: float | None = None
    handle_thickness_mm: float | None = None
    handle_length_mm: float | None = None
    screw_piece_dia_mm: float | None = None
    screw_piece_length_mm: float | None = None
    screw_piece_qty: int | None = None
    chair_angle_weight_per_m: float | None = None
    chair_angle_length_mm: float | None = None
    cutting_length_mm: float | None = None
    cutting_surface_count: int | None = None
    weld_length_mm: float | None = None
    bend_count: int | None = None
    confidence: float = 0
    notes: list[str] = []
    source: Literal["gemini_api"] = "gemini_api"

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]


class EstimateResponse(BaseModel):
    part_name: str
    likely_use: str
    uploaded_file: str
    file_size_kb: float
    total_weight_kg: float
    total_material_cost: float
    total_process_cost: float
    surface_treatment_cost: float
    total_estimated_cost: float
    material_summary: MaterialSummary
    stock_summary: StockSummary
    assumptions: list[str]
    items: list[LineItem]
    process_breakdown: ProcessBreakdown
    calculation_steps: list[CalculationStep]


class ExtractedPartDimensions(BaseModel):
    length_mm: float | None = None
    width_or_outer_dia_mm: float | None = None
    secondary_width_mm: float | None = None
    thickness_or_wall_thickness_mm: float | None = None


class ExtractedCuttingMetrics(BaseModel):
    laser_cutting_length_mm: float = 0
    press_machine_hits_count: int = 0


class NestingLayoutHint(BaseModel):
    nesting_strategy: str = "NA"
    recommended_grain_or_cut_direction: str = "NA"


class ExtractedCostPart(BaseModel):
    part_number: str
    component_name: str | None = None
    component_type: str
    tube_type: str = "NA"
    material_type: str | None = None
    material_code: str | None = None
    per_set_qty: int = 1
    dimensions: ExtractedPartDimensions = Field(default_factory=ExtractedPartDimensions)
    bends_per_part: int = 0
    cutting_metrics: ExtractedCuttingMetrics = Field(default_factory=ExtractedCuttingMetrics)
    nesting_layout_hint: NestingLayoutHint = Field(default_factory=NestingLayoutHint)
    notes: list[str] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_part_notes(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    @field_validator("tube_type", mode="before")
    @classmethod
    def normalize_tube_type(cls, value: object) -> str:
        if value is None or value == "":
            return "NA"
        return str(value)


class ExtractedAssemblyFabrication(BaseModel):
    total_assembly_welding_length_mm: float = 0
    notes: list[str] = Field(default_factory=list)


class StructuredExtraction(BaseModel):
    currency: str = "INR"
    part_name: str | None = None
    raw_material_type: str | None = None
    raw_material_code: str | None = None
    per_part_breakdown: list[ExtractedCostPart] = Field(default_factory=list)
    assembly_level_fabrication: ExtractedAssemblyFabrication = Field(default_factory=ExtractedAssemblyFabrication)
    confidence: float = 0
    notes: list[str] = Field(default_factory=list)


class WeightLedger(BaseModel):
    unit_gross_rm_weight_kg: float
    unit_net_finished_weight_kg: float
    unit_scrap_waste_weight_kg: float
    total_set_gross_weight_kg: float


class CalculatedCosts(BaseModel):
    material_cost: float
    laser_cutting_cost_estimate: float
    machine_punching_cost_estimate: float
    bending_cost: float
    painting_cost: float
    total_single_part_cost_via_laser: float
    total_single_part_cost_via_machine: float
    total_combined_set_cost_via_laser: float
    total_combined_set_cost_via_machine: float


class CostedPartBreakdown(ExtractedCostPart):
    surface_area_sq_meter: float
    weight_ledger: WeightLedger
    calculated_costs: CalculatedCosts
    calculation_steps: list[CalculationStep] = Field(default_factory=list)


class AssemblyLevelFabrication(BaseModel):
    total_assembly_welding_length_mm: float
    welding_labor_cost: float
    tacking_fixed_setup_cost: float
    grand_total_assembly_cost_via_laser: float
    grand_total_assembly_cost_via_machine: float


class StructuredCostBreakdown(BaseModel):
    currency: str = "INR"
    part_name: str | None = None
    per_part_breakdown: list[CostedPartBreakdown]
    assembly_level_fabrication: AssemblyLevelFabrication
    assumptions: list[str] = Field(default_factory=list)
