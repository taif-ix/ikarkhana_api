from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class ExtractedComponent(BaseModel):
    part_number: str
    component_type: str = Field(description="Component manufacturing classification")
    description: Optional[str] = ""
    material: str = Field(default="UNKNOWN", description="Material grade such as CRCA, MS, SS304, or aluminium")
    per_set_qty: int
    part_length_mm: float
    part_width_mm: float
    part_height_mm: float = Field(default=0.0, description="Overall part height when separately dimensioned")
    thickness_mm: float
    number_of_bends_per_part: int
    position_z_mm: float = 5.0
    estimated_punched_slots_count: int = 0
    is_tapered_profile: bool = False
    weld_seams_count: int = 0


class ExtractedBOMAssembly(BaseModel):
    current_drawing_id: str
    components: List[ExtractedComponent]
    total_estimated_welding_length_mm: float
    referenced_drawing_ids: List[str] = Field(default_factory=list)
    target_blueprint_weight_kg: Optional[float] = None
    preferred_assembly_side: str = "left"


class DrawingExtractionRequest(BaseModel):
    image_url: HttpUrl
    filename: Optional[str] = None
    job_id: Optional[str] = Field(default=None, max_length=128)


class BatchFileRequest(BaseModel):
    analysis_id: int
    file_url: HttpUrl
    filename: Optional[str] = None


class BatchExtractionRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    callback_url: HttpUrl
    files: List[BatchFileRequest] = Field(min_length=1, max_length=100)


class BatchAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["ACCEPTED"] = "ACCEPTED"
    file_count: int


class AnalysisSummary(BaseModel):
    part_number: str
    part_description: str
    part_type: str
    material: str
    thickness: float
    width: float
    height: float
    length: float
    drawing_weight_kg: float
    calculated_weight_kg: float
    machine_time_seconds: int
    sheet_usage_percent: float
    material_cost: float
    net_material_weight_kg: float
    scrap_weight_kg: float
    scrap_cost: float


class BomItem(BaseModel):
    item_name: str
    item_class: str
    quantity: float
    unit: Literal["kg"] = "kg"
    weight_kg: float
    unit_cost: float
    total_cost: float
    is_scrap: bool = False


class AnalysisCallbackResponse(BaseModel):
    job_id: str
    analysis_id: int
    status: Literal["COMPLETED"] = "COMPLETED"
    analysis: AnalysisSummary
    bom: List[BomItem]


class AnalysisFailureCallback(BaseModel):
    job_id: str
    analysis_id: int
    status: Literal["FAILED"] = "FAILED"
    error: str
