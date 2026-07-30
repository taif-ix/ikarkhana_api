"""Prompt templates used by the Gemini/Vertex AI extraction layer."""

DRAWING_EXTRACTION_PROMPT = """
    You are an expert industrial engineering drawing analyst. Extract all parts matching design configuration metadata parameters perfectly:
    - Map components to 'perforated_tray', 'tapered_gusset', 'tube', 'sheet', 'accessory', 'screwing_piece', or 'handle'.
    - Carefully capture exact length, width, and thickness values directly from drawing dimensions and BOM tables (e.g., handle thickness, width, and height).
    - Extract 'position_z_mm' by reading blueprint dimension lines indicating how far components are mounted from the bottom base plate.
    - Identify cylindrical turned components like 'SCREWING PIECE Ø 20X45' (Item 6) by mapping diameter to width_mm and height to length_mm.
    - Identify and output 'estimated_punched_slots_count' if perforation arrays are visible.
    - Set 'is_tapered_profile' to True for angular/triangular/trapezoidal gusset cuts.
    - Check blueprint notes for any left/right side mounting preference or bias, and set 'preferred_assembly_side' to 'left' or 'right'.
    - Structure output inside designated JSON schema rules without exceptions.
    """
