from typing import List, Optional

from pydantic import BaseModel, Field


# AI extraction response schemas.
# =====================================================================
class ExtractedComponent(BaseModel):
    part_number: str
    component_type: str = Field(description="Must be explicitly 'perforated_tray', 'tapered_gusset', 'tube', 'sheet', 'accessory', 'screwing_piece', or 'handle'")
    description: Optional[str] = Field(default="", description="Exact text from BOM description column, e.g., 'CHAIR ANGLE-RH', 'HANDLE', or 'SCREWING PIECE Ø 20X45'")
    per_set_qty: int
    part_length_mm: float = Field(description="Height, linear length, or vertical back height of the part")
    part_width_mm: float = Field(description="Width, outer diameter, or minor dimension of the part")
    thickness_mm: float = Field(description="Exact material thickness extracted from drawing callout (e.g., 2mm for handle)")
    number_of_bends_per_part: int
    position_z_mm: float = Field(default=5.0, description="Exact vertical mounting distance or offset from the bottom base plate in mm as shown in drawing dimensions")
    estimated_punched_slots_count: int = Field(default=0, description="Total count of punched slots/perforations visible on surface area")
    is_tapered_profile: bool = Field(default=False, description="True if part features a non-rectangular trapezoidal or triangular cut path")
    weld_seams_count: int = Field(default=0, description="Total number of structural weld locations required")
    

class ExtractedBOMAssembly(BaseModel):
    current_drawing_id: str = Field(description="Primary drawing identifier extracted from title block.")
    components: List[ExtractedComponent]
    total_estimated_welding_length_mm: float
    referenced_drawing_ids: List[str] = Field(default=[])
    target_blueprint_weight_kg: Optional[float] = Field(default=None)
    preferred_assembly_side: str = Field(default="left", description="Indicates preferred side orientation or mounting bias, e.g., 'left' or 'right' based on drawing notes.")
