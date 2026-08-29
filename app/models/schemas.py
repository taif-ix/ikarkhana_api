from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

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
    width_mm: float | None = Field(default=None, validation_alias=AliasChoices("width_mm", "width_or_outer_dia_mm"))
    height_mm: float | None = Field(default=None, validation_alias=AliasChoices("height_mm", "secondary_width_mm"))
    outer_diameter_mm: float | None = None
    thickness_mm: float | None = Field(default=None, validation_alias=AliasChoices("thickness_mm", "thickness_or_wall_thickness_mm"))


class CalculatedCuttingMetrics(BaseModel):
    laser_cutting_length_mm: float = 0
    press_machine_hits_count: int = 0
    outer_profile_cut_length_mm: float = 0
    internal_feature_cut_length_mm: float = 0
    internal_feature_count: int = 0

    @field_validator(
        "laser_cutting_length_mm",
        "press_machine_hits_count",
        "outer_profile_cut_length_mm",
        "internal_feature_cut_length_mm",
        "internal_feature_count",
        mode="before",
    )
    @classmethod
    def normalize_missing_cutting_metric(cls, value: object) -> object:
        if value is None or value == "":
            return 0
        return value


class ExtractedHole(BaseModel):
    hole_type: str = "plain"
    diameter_mm: float | None = None
    quantity_per_part: int | None = None
    through: bool | None = None


class ExtractedSlot(BaseModel):
    slot_type: str = "obround"
    length_mm: float | None = None
    width_mm: float | None = None
    quantity_per_part: int | None = None
    through: bool | None = None


class ExtractedThread(BaseModel):
    thread_size: str | None = None
    nominal_diameter_mm: float | None = None
    quantity_per_part: int | None = None
    through: bool | None = None
    thread_depth_mm: float | None = None


class ExtractedRectangularFeature(BaseModel):
    length_mm: float | None = None
    width_mm: float | None = None
    quantity_per_part: int | None = None


class ExtractedChamfer(BaseModel):
    size_mm: float | None = None
    angle_deg: float | None = None
    quantity_per_part: int | None = None


class ExtractedBend(BaseModel):
    angle_deg: float | None = None
    inside_radius_mm: float | None = None
    bend_length_mm: float | None = None


class GeometryPoint(BaseModel):
    x: float
    y: float


class ExtractedOuterContour(BaseModel):
    geometry_type: str = "polygon"
    points_mm: list[GeometryPoint] = Field(default_factory=list)


class ExtractedFlatPattern(BaseModel):
    outer_contour: ExtractedOuterContour | None = None
    holes: list[ExtractedHole] = Field(default_factory=list)
    slots: list[ExtractedSlot] = Field(default_factory=list)
    threads: list[ExtractedThread] = Field(default_factory=list)
    notches: list[ExtractedRectangularFeature] = Field(default_factory=list)
    cutouts: list[ExtractedRectangularFeature] = Field(default_factory=list)
    chamfers: list[ExtractedChamfer] = Field(default_factory=list)
    bend_lines: list[ExtractedBend] = Field(default_factory=list)

class ExtractedProfile(BaseModel):
    shape: str
    is_hollow: bool


class NestingConstraints(BaseModel):
    grain_direction: str | None = None
    rotation_allowed: bool | None = None
    mirror_pair_required: bool | None = None


class NestingLayoutHint(BaseModel):
    nesting_strategy: str = "NA"
    recommended_grain_or_cut_direction: str = "NA"

    @field_validator("nesting_strategy", "recommended_grain_or_cut_direction", mode="before")
    @classmethod
    def normalize_missing_nesting_text(cls, value: object) -> str:
        if value is None or value == "":
            return "NA"
        return str(value)


class PartImageRegion(BaseModel):
    x_min: float | None = None
    y_min: float | None = None
    x_max: float | None = None
    y_max: float | None = None
    source: str = "NULL - Insufficient Data"

    @field_validator("source", mode="before")
    @classmethod
    def normalize_missing_region_source(cls, value: object) -> str:
        if value is None or value == "":
            return "NULL - Insufficient Data"
        return str(value)


class ExtractedCostPart(BaseModel):
    part_number: str
    component_name: str | None = None
    component_type: str
    profile: ExtractedProfile | None = None
    material_type: str | None = None
    material_code: str | None = None
    material_grade: str | None = None
    material_specification: str | None = None
    per_set_qty: int = 1
    dimensions: ExtractedPartDimensions = Field(default_factory=ExtractedPartDimensions)
    holes: list[ExtractedHole] = Field(default_factory=list)
    slots: list[ExtractedSlot] = Field(default_factory=list)
    threads: list[ExtractedThread] = Field(default_factory=list)
    notches: list[ExtractedRectangularFeature] = Field(default_factory=list)
    cutouts: list[ExtractedRectangularFeature] = Field(default_factory=list)
    chamfers: list[ExtractedChamfer] = Field(default_factory=list)
    bends: list[ExtractedBend] = Field(default_factory=list)
    flat_pattern: ExtractedFlatPattern | None = None
    bends_per_part: int | None = None
    referenced_drawing_number: str | None = None
    nesting_constraints: NestingConstraints | None = None

    @field_validator("part_number", "component_type", mode="before")
    @classmethod
    def normalize_required_part_text(cls, value: object) -> str:
        if value is None or value == "":
            return "unknown"
        return str(value)

    @field_validator("per_set_qty", mode="before")
    @classmethod
    def normalize_missing_qty(cls, value: object) -> object:
        if value is None or value == "":
            return 1
        return value

    @field_validator("bends_per_part", mode="before")
    @classmethod
    def normalize_missing_bends(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value


class ExtractedAssemblyFabrication(BaseModel):
    welding_length_mm: float | None = Field(default=None, validation_alias=AliasChoices("welding_length_mm", "total_assembly_welding_length_mm"))

    @field_validator("welding_length_mm", mode="before")
    @classmethod
    def normalize_missing_weld_length(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value


class ReferencedDrawing(BaseModel):
    drawing_number: str
    file_name_hint: str | None = None
    referenced_by_part_number: str | None = None
    referenced_by_component: str | None = None
    reason: str = "Child/detail drawing is referenced but not included in this upload."
    required_for_costing: bool = True

    @field_validator("drawing_number", mode="before")
    @classmethod
    def normalize_drawing_number(cls, value: object) -> str:
        if value is None or value == "":
            return "UNKNOWN"
        return str(value)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reference_reason(cls, value: object) -> str:
        if value is None or value == "":
            return "Child/detail drawing is referenced but not included in this upload."
        return str(value)

    @field_validator("required_for_costing", mode="before")
    @classmethod
    def normalize_required_for_costing(cls, value: object) -> object:
        if value is None or value == "":
            return True
        return value


class ReferenceExtraction(BaseModel):
    drawing_number: str | None = None
    file_name_hint: str | None = None
    referenced_drawings: list[ReferencedDrawing] = Field(default_factory=list)
    confidence: float = 0
    notes: list[str] = Field(default_factory=list)


class BatchReferenceItem(BaseModel):
    file_name: str
    file_size_kb: float
    drawing_number: str | None = None
    referenced_drawings: list[ReferencedDrawing] = Field(default_factory=list)
    confidence: float = 0
    notes: list[str] = Field(default_factory=list)


class BatchReferenceExtraction(BaseModel):
    files: list[BatchReferenceItem] = Field(default_factory=list)


class StructuredExtraction(BaseModel):
    raw_material_type: str | None = None
    raw_material_code: str | None = None
    per_part_breakdown: list[ExtractedCostPart] = Field(default_factory=list)
    assembly_fabrication: ExtractedAssemblyFabrication = Field(
        default_factory=ExtractedAssemblyFabrication,
        validation_alias=AliasChoices("assembly_fabrication", "assembly_level_fabrication"),
    )


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
    tube_type: str = "NA"
    image_region: PartImageRegion = Field(default_factory=PartImageRegion)
    nesting_layout_hint: NestingLayoutHint = Field(default_factory=NestingLayoutHint)
    notes: list[str] = Field(default_factory=list)
    cutting_metrics: CalculatedCuttingMetrics = Field(default_factory=CalculatedCuttingMetrics)
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
    raw_material_type: str | None = None
    raw_material_code: str | None = None
    per_part_breakdown: list[CostedPartBreakdown]
    assembly_level_fabrication: AssemblyLevelFabrication
    referenced_drawings: list[ReferencedDrawing] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
