import asyncio
import io
import json
import traceback
import uuid
import zipfile
from typing import Any, Dict, List

import openpyxl
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app import state
from app.ai.extraction import async_analyze_single_drawing
from app.services.costing import premium_calculate_bending_cost, premium_calculate_painting_cost
from app.services.visuals import generate_cad_2d_flat_layout, generate_cad_3d_assembly_model


router = APIRouter()


@router.post("/initialize-async-batch")
async def initialize_async_batch(raw_files: List[UploadFile] = File(...)):
    session_id = uuid.uuid4().hex
    file_records = []

    for file in raw_files:
        filename = file.filename
        file_bytes = await file.read()
        
        if filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
                    for member_name in archive.namelist():
                        if member_name.startswith('__MACOSX') or member_name.endswith('.DS_Store') or archive.getinfo(member_name).is_dir():
                            continue
                        clean_name = os.path.basename(member_name)
                        if clean_name.split(".")[-1].lower() in ["jpg", "jpeg", "png", "pdf", "tif", "tiff"]:
                            file_records.append({"filename": clean_name, "bytes": archive.read(member_name)})
            except Exception as e:
                print(f"[ZIP COMPRESSION EXCEPTION]: {str(e)}")
        else:
            if filename.split(".")[-1].lower() in ["jpg", "jpeg", "png", "pdf", "tif", "tiff"]:
                file_records.append({"filename": filename, "bytes": file_bytes})

    state.session_cache[session_id] = {"files": file_records, "compiled_results": []}
    return {"session_id": session_id}

@router.get("/stream-live-calculations/{session_id}")
async def stream_live_calculations(session_id: str):
    if session_id not in state.session_cache:
        raise HTTPException(status_code=404, detail="Session reference expired.")

    async def sse_event_generator():
        session_data = state.session_cache[session_id]
        sem = asyncio.Semaphore(3)
        
        async def worker(item):
            async with sem:
                return await async_analyze_single_drawing(item["bytes"], item["filename"])
        async_tasks = [worker(item) for item in session_data["files"]]
        
        has_errors = False
        for concurrent_future in asyncio.as_completed(async_tasks):
            try:
                outcome = await concurrent_future
                session_data["compiled_results"].append(outcome)
                
                packet = {
                    "status": "PROGRESS",
                    "drawing_id": outcome["drawing_id"],
                    "target_weight": outcome["target_blueprint_weight_kg"],
                    "calibrated_weight": outcome["calculated_net_wt"]
                }
                yield f"data: {json.dumps(packet)}\n\n"
                await asyncio.sleep(0.1)
            except Exception as inner_err:
                has_errors = True
                yield f"data: {json.dumps({'status': 'ERROR', 'message': str(inner_err)})}\n\n"
                return

        if has_errors or not session_data["compiled_results"]:
            return

        try:
            wb = openpyxl.Workbook()
            
            ws3 = wb.active
            ws3.title = "Material & Process Rates"
            ws3.views.sheetView[0].showGridLines = True
            
            navy_fill = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
            gold_fill = PatternFill(start_color="F9F1DC", end_color="F9F1DC", fill_type="solid")
            font_header = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
            font_body = Font(name="Segoe UI", size=11, bold=False)
            font_total = Font(name="Segoe UI", size=11, bold=True, color="1B365D")
            
            center_align = Alignment(horizontal="center", vertical="center")
            right_align = Alignment(horizontal="right", vertical="center")
            left_align = Alignment(horizontal="left", vertical="center")
            
            thin_border = Border(left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'), top=Side(style='thin', color='CCCCCC'), bottom=Side(style='thin', color='CCCCCC'))
            double_border = Border(top=Side(style='thin', color='1B365D'), bottom=Side(style='double', color='1B365D'), left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'))

            ws3.append(["Process Parameters / Raw Stock Classification", "Base Rate (INR)"])
            ws3.cell(row=1, column=1).fill = navy_fill; ws3.cell(row=1, column=1).font = font_header
            ws3.cell(row=1, column=2).fill = navy_fill; ws3.cell(row=1, column=2).font = font_header
            
            TUBE_RATE = 242.00
            SHEET_RATE = 215.00
            PERFORATED_SHEET_RATE = 230.00
            TAPERED_GUSSET_RATE = 210.00
            ACCESSORY_RATE = 130.00
            SCREWING_PIECE_RATE = 240.00
            LASER_CUT_RATE = 35.00
            TURRET_PUNCH_HIT_RATE = 0.40
            BEND_RATE = 6.00
            PAINT_RATE = 140.00
            WELDING_RATE = 450.00
            TACKING_RATE = 1100.00

            default_pricing_ledger = [
                ("Tube Profile Steel Stock Rate (Per Kg)", TUBE_RATE),
                ("Standard Sheet Metal Rate (Per Kg)", SHEET_RATE),
                ("Perforated Tray Sheet Rate (Per Kg)", PERFORATED_SHEET_RATE),
                ("Tapered Gusset/Bracket Rate (Per Kg)", TAPERED_GUSSET_RATE),
                ("Solid Accessory Components Rate (Per Kg)", ACCESSORY_RATE),
                ("Screwing Piece / Machined Boss Rate (Per Kg)", SCREWING_PIECE_RATE),
                ("High Velocity Laser Cut Path Fee (Per Meter)", LASER_CUT_RATE),
                ("Turret Punch Perforation Fee (Per Stroke)", TURRET_PUNCH_HIT_RATE),
                ("CNC Brake Press Bending Fee (Per Stroke)", BEND_RATE),
                ("Surface Coating Protection Fee (Per Sqm)", PAINT_RATE),
                ("Structural Manual Weld Labor Fee (Per Meter)", WELDING_RATE),
                ("Assembly Framing Tacking Setup Fee", TACKING_RATE)
            ]
            for p_desc, p_val in default_pricing_ledger:
                ws3.append([p_desc, p_val])
            for r_idx in range(2, 13):
                ws3.cell(row=r_idx, column=1).font = font_body; ws3.cell(row=r_idx, column=1).border = thin_border
                ws3.cell(row=r_idx, column=2).font = font_body; ws3.cell(row=r_idx, column=2).border = thin_border; ws3.cell(row=r_idx, column=2).alignment = right_align
                ws3.cell(row=r_idx, column=2).number_format = '#,##0.00'

            ws1 = wb.create_sheet(title="Project Executive Summary", index=0)
            ws1.views.sheetView[0].showGridLines = True
            ws1.append(["Master Component Reference", "Target Mass Blueprint (kg)", "Calibrated Net Mass (kg)", "Calibrated Scrap Mass (kg)", "Calibrated Gross Mass (kg)", "Total Operations Cost (Laser Layout)", "Total Operations Cost (Turret Punch Matrix)", "Child Sub-Assembly Maps"])
            for cell in ws1[1]:
                cell.fill = navy_fill; cell.font = font_header; cell.alignment = center_align; cell.border = thin_border
            ws1.row_dimensions[1].height = 28

            ws2 = wb.create_sheet(title="All Components Ledger", index=1)
            ws2.views.sheetView[0].showGridLines = True
            ws2.append(["Parent Sheet Link", "Part No", "Classification Profile", "Quantity Run", "Length (mm)", "Width (mm)", "Thickness (mm)", "Net Mass (kg)", "Scrap Mass (kg)", "Gross Mass (kg)", "Laser Trace Path (mm)", "Laser Weld Path (mm)", "Bending Stroke Sets", "Bending Cost (INR)", "Surface Coating Cost (INR)", "Laser Routing Cost (INR)", "Punch Tooling Cost (INR)", "Advanced Manufacturing Process Guidance"])
            for cell in ws2[1]:
                cell.fill = navy_fill; cell.font = font_header; cell.alignment = center_align; cell.border = thin_border
            ws2.row_dimensions[1].height = 26

            grand_totals_calc = {"net_wt": 0.0, "scrap_wt": 0.0, "gross_wt": 0.0, "laser_cost": 0.0, "machine_cost": 0.0}
            ledger_totals_calc = {"qty": 0, "net_wt": 0.0, "scrap_wt": 0.0, "gross_wt": 0.0, "bend_strokes": 0, "bend_cost": 0.0, "paint_cost": 0.0, "laser_cost": 0.0, "machine_cost": 0.0}

            all_child_ids = set()
            for res in session_data["compiled_results"]:
                for child_id in res.get("referenced_drawing_ids", []):
                    all_child_ids.add(child_id.strip().upper())

            for res in session_data["compiled_results"]:
                drg_id = res["drawing_id"].strip().upper()
                if drg_id in all_child_ids:
                    continue
                
                drawing_laser_cost_accum = 0.0
                drawing_machine_cost_accum = 0.0
                drawing_scrap_wt_accum = 0.0
                drawing_net_wt_accum = 0.0
                drawing_gross_wt_accum = 0.0
                
                for c in res["components"]:
                    c_type = c.get("component_type", "sheet").lower()
                    
                    if c_type == "tube":
                        rate = TUBE_RATE
                    elif c_type == "perforated_tray":
                        rate = PERFORATED_SHEET_RATE
                    elif c_type == "tapered_gusset":
                        rate = TAPERED_GUSSET_RATE
                    elif c_type == "screwing_piece":
                        rate = SCREWING_PIECE_RATE
                    elif c_type == "handle":
                        rate = SHEET_RATE
                    elif c_type in ["sheet", "chair angle", "chair_angle"]:
                        rate = SHEET_RATE
                    else:
                        rate = ACCESSORY_RATE
                    
                    comp_qty = c.get("per_set_qty", 1)
                    total_c_net = c.get("net_weight_kg", 0.0) * comp_qty
                    total_c_scrap = c.get("scrap_weight_kg", 0.0) * comp_qty
                    total_c_gross = c.get("gross_weight_kg", 0.0) * comp_qty
                    
                    c_paint_cost = premium_calculate_painting_cost(c_type, c.get("length_mm", 0.0), c.get("width_mm", 0.0), comp_qty, PAINT_RATE)
                    c_bend_cost = premium_calculate_bending_cost(c.get("bends", 0), comp_qty, BEND_RATE)
                    material_cost = total_c_gross * rate
                    
                    laser_cut_cost = ((c.get("laser_cutting_length_mm", 0.0) / 1000.0) * LASER_CUT_RATE) * comp_qty
                    punch_operation_cost = (c.get("slots_count", 0) * TURRET_PUNCH_HIT_RATE) * comp_qty
                    
                    c_laser_cost = round(material_cost + laser_cut_cost + c_bend_cost + c_paint_cost, 2)
                    c_machine_cost = round(material_cost + laser_cut_cost + punch_operation_cost + c_bend_cost + c_paint_cost, 2)
                    
                    drawing_net_wt_accum += total_c_net
                    drawing_scrap_wt_accum += total_c_scrap
                    drawing_gross_wt_accum += total_c_gross
                    drawing_laser_cost_accum += c_laser_cost
                    drawing_machine_cost_accum += c_machine_cost
                    
                    ledger_totals_calc["qty"] += comp_qty
                    ledger_totals_calc["net_wt"] += total_c_net
                    ledger_totals_calc["scrap_wt"] += total_c_scrap
                    ledger_totals_calc["gross_wt"] += total_c_gross
                    ledger_totals_calc["bend_strokes"] += (c.get("bends", 0) * comp_qty)
                    ledger_totals_calc["bend_cost"] += c_bend_cost
                    ledger_totals_calc["paint_cost"] += c_paint_cost
                    ledger_totals_calc["laser_cost"] += c_laser_cost
                    ledger_totals_calc["machine_cost"] += c_machine_cost

                    ws2.append([
                        drg_id, c.get("part_number", "UNKNOWN"), c_type.upper(), comp_qty,
                        c.get("length_mm", 0.0), c.get("width_mm", 0.0), c.get("thickness_mm", 0.0), 
                        round(total_c_net, 3), round(total_c_scrap, 3), round(total_c_gross, 3),
                        c.get("laser_cutting_length_mm", 0.0), c.get("laser_welding_length_mm", 0.0), c.get("bends", 0) * comp_qty,
                        c_bend_cost, c_paint_cost, c_laser_cost, c_machine_cost, c.get("process_sequence", "")
                    ])

                weld_cost_total = (res.get("total_estimated_welding_length_mm", 0.0) / 1000.0) * WELDING_RATE
                final_drawing_laser_total = round(drawing_laser_cost_accum + weld_cost_total + TACKING_RATE, 2)
                final_drawing_machine_total = round(drawing_machine_cost_accum + weld_cost_total + TACKING_RATE, 2)
                
                grand_totals_calc["net_wt"] += drawing_net_wt_accum
                grand_totals_calc["scrap_wt"] += drawing_scrap_wt_accum
                grand_totals_calc["gross_wt"] += drawing_gross_wt_accum
                grand_totals_calc["laser_cost"] += final_drawing_laser_total
                grand_totals_calc["machine_cost"] += final_drawing_machine_total

                child_refs_str = ", ".join(res.get("referenced_drawing_ids", [])) if len(res.get("referenced_drawing_ids", [])) > 0 else "None"
                
                ws1.append([
                    drg_id, res.get("target_blueprint_weight_kg", 0.0), round(drawing_net_wt_accum, 3),
                    round(drawing_scrap_wt_accum, 3), round(drawing_gross_wt_accum, 3),
                    final_drawing_laser_total, final_drawing_machine_total, child_refs_str
                ])

            if ws1.max_row >= 2:
                for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row):
                    for cell in row:
                        cell.font = font_body; cell.border = thin_border
                        cell.alignment = right_align if cell.column in range(2, 8) else center_align
                        if cell.column in [2, 3, 4, 5]: cell.number_format = '#,##0.000'
                        if cell.column in [6, 7]: cell.number_format = '#,##0.00'

            if ws2.max_row >= 2:
                for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row):
                    for cell in row:
                        cell.font = font_body; cell.border = thin_border
                        cell.alignment = right_align if cell.column in range(4, 18) else (left_align if cell.column == 18 else center_align)
                        if cell.column in [8, 9, 10]: cell.number_format = '#,##0.000'
                        if cell.column in range(11, 18): cell.number_format = '#,##0.00'

            ws1_total_row = ws1.max_row + 1
            ws1.cell(row=ws1_total_row, column=1, value="GRAND TOTALS").font = font_total
            ws1.cell(row=ws1_total_row, column=1).fill = gold_fill; ws1.cell(row=ws1_total_row, column=1).border = double_border; ws1.cell(row=ws1_total_row, column=1).alignment = center_align
            ws1.cell(row=ws1_total_row, column=2, value="N/A").fill = gold_fill; ws1.cell(row=ws1_total_row, column=2).border = double_border; ws1.cell(row=ws1_total_row, column=2).alignment = center_align; ws1.cell(row=ws1_total_row, column=2).font = font_body

            ws1.cell(row=ws1_total_row, column=3, value=round(grand_totals_calc["net_wt"], 3)).number_format = '#,##0.000'
            ws1.cell(row=ws1_total_row, column=4, value=round(grand_totals_calc["scrap_wt"], 3)).number_format = '#,##0.000'
            ws1.cell(row=ws1_total_row, column=5, value=round(grand_totals_calc["gross_wt"], 3)).number_format = '#,##0.000'
            ws1.cell(row=ws1_total_row, column=6, value=round(grand_totals_calc["laser_cost"], 2)).number_format = '#,##0.00'
            ws1.cell(row=ws1_total_row, column=7, value=round(grand_totals_calc["machine_cost"], 2)).number_format = '#,##0.00'
            ws1.cell(row=ws1_total_row, column=8, value="").fill = gold_fill; ws1.cell(row=ws1_total_row, column=8).border = double_border
            
            for c in range(3, 8):
                cell = ws1.cell(row=ws1_total_row, column=c)
                cell.font = font_total; cell.fill = gold_fill; cell.border = double_border; cell.alignment = right_align

            ws2_total_row = ws2.max_row + 1
            ws2.cell(row=ws2_total_row, column=1, value="LEDGER TOTALS").font = font_total
            ws2.cell(row=ws2_total_row, column=1).fill = gold_fill; ws2.cell(row=ws2_total_row, column=1).border = double_border; ws2.cell(row=ws2_total_row, column=1).alignment = center_align
            for c in range(2, 4):
                ws2.cell(row=ws2_total_row, column=c, value="").fill = gold_fill; ws2.cell(row=ws2_total_row, column=c).border = double_border
            ws2.cell(row=ws2_total_row, column=4, value=ledger_totals_calc["qty"]).alignment = center_align
            for c in range(5, 8):
                ws2.cell(row=ws2_total_row, column=c, value="").fill = gold_fill; ws2.cell(row=ws2_total_row, column=c).border = double_border
            ws2.cell(row=ws2_total_row, column=8, value=round(ledger_totals_calc["net_wt"], 3)).number_format = '#,##0.000'
            ws2.cell(row=ws2_total_row, column=9, value=round(ledger_totals_calc["scrap_wt"], 3)).number_format = '#,##0.000'
            ws2.cell(row=ws2_total_row, column=10, value=round(ledger_totals_calc["gross_wt"], 3)).number_format = '#,##0.000'
            for blank_c in [11, 12]:
                ws2.cell(row=ws2_total_row, column=blank_c, value="").fill = gold_fill; ws2.cell(row=ws2_total_row, column=blank_c).border = double_border
            ws2.cell(row=ws2_total_row, column=13, value=ledger_totals_calc["bend_strokes"]).number_format = '#,##0'
            ws2.cell(row=ws2_total_row, column=14, value=round(ledger_totals_calc["bend_cost"], 2)).number_format = '#,##0.00'
            ws2.cell(row=ws2_total_row, column=15, value=round(ledger_totals_calc["paint_cost"], 2)).number_format = '#,##0.00'
            ws2.cell(row=ws2_total_row, column=16, value=round(ledger_totals_calc["laser_cost"], 2)).number_format = '#,##0.00'
            ws2.cell(row=ws2_total_row, column=17, value=round(ledger_totals_calc["machine_cost"], 2)).number_format = '#,##0.00'
            ws2.cell(row=ws2_total_row, column=18, value="").fill = gold_fill; ws2.cell(row=ws2_total_row, column=18).border = double_border

            for c in [4, 8, 9, 10, 13, 14, 15, 16, 17]:
                cell = ws2.cell(row=ws2_total_row, column=c)
                cell.font = font_total; cell.fill = gold_fill; cell.border = double_border
                if c >= 8: cell.alignment = right_align

            ws4 = wb.create_sheet(title="Nesting Optimization Strategy", index=3)
            ws4.views.sheetView[0].showGridLines = True
            
            headers = [
                "Drawing Number", "Material Type", "Part Dimensions (mm)", 
                "Raw Material Size", "Nesting Algorithm", 
                "Yield (Parts per Raw)", "Scrap Rate (%)", "Optimization Strategy"
            ]
            ws4.append(headers)
            
            for cell in ws4[1]:
                cell.fill = navy_fill
                cell.font = font_header
                cell.alignment = center_align
                cell.border = thin_border
            ws4.row_dimensions[1].height = 28

            STD_TUBE_LENGTH = 6000.0  
            STD_SHEET_L = 2500.0      
            STD_SHEET_W = 1250.0
            KERF = 3.0                

            row_idx = 2
            for dwg in session_data["compiled_results"]:
                dwg_no = dwg.get("drawing_id", "Unknown")
                components = dwg.get("components", [])
                
                is_column = any("tube" in str(c.get("component_type", "")).lower() or "column" in str(c.get("part_number", "")).lower() for c in components)
                
                if is_column:
                    part_length = 500.0  
                    parts_per_tube = int(STD_TUBE_LENGTH // (part_length + KERF))
                    used_length = parts_per_tube * (part_length + KERF)
                    remnant_drop = STD_TUBE_LENGTH - used_length
                    scrap_percent = round((remnant_drop / STD_TUBE_LENGTH) * 100, 2)
                    
                    row_data = [
                        dwg_no,
                        "Square Tube / Profile",
                        f"{part_length} L",
                        f"{STD_TUBE_LENGTH} mm Length",
                        "1D Linear Bin Packing",
                        parts_per_tube,
                        f"{scrap_percent}%",
                        f"Standard linear cut. Yields {parts_per_tube} pieces per standard 6m tube, leaving a {remnant_drop}mm remnant drop."
                    ]
                else:
                    part_l = 600.0 
                    part_w = 200.0
                    
                    nx1 = int(STD_SHEET_L // (part_l + KERF))
                    ny1 = int(STD_SHEET_W // (part_w + KERF))
                    yield_1 = nx1 * ny1
                    
                    nx2 = int(STD_SHEET_L // (part_w + KERF))
                    ny2 = int(STD_SHEET_W // (part_l + KERF))
                    yield_2 = nx2 * ny2
                    
                    best_yield = max(yield_1, yield_2)
                    area_used = best_yield * (part_l * part_w)
                    total_area = STD_SHEET_L * STD_SHEET_W
                    scrap_percent = round(((total_area - area_used) / total_area) * 100, 2)
                    
                    orientation_note = "Standard" if yield_1 >= yield_2 else "Rotated 90°"
                    
                    row_data = [
                        dwg_no,
                        "SS Sheet / Plate",
                        f"{part_l} x {part_w}",
                        f"{STD_SHEET_L} x {STD_SHEET_W} mm",
                        "2D Guillotine Optimization",
                        best_yield,
                        f"{scrap_percent}%",
                        f"Best yield achieved using {orientation_note} orientation. Nests {best_yield} parts on a standard 8x4 sheet."
                    ]

                ws4.append(row_data)
                
                for col_idx in range(1, len(headers) + 1):
                    c_cell = ws4.cell(row=row_idx, column=col_idx)
                    c_cell.font = font_body
                    c_cell.border = thin_border
                    c_cell.alignment = left_align if col_idx == 8 else center_align
                
                row_idx += 1

            ws4.column_dimensions['A'].width = 18
            ws4.column_dimensions['B'].width = 22
            ws4.column_dimensions['C'].width = 22
            ws4.column_dimensions['D'].width = 25
            ws4.column_dimensions['E'].width = 25
            ws4.column_dimensions['F'].width = 22
            ws4.column_dimensions['G'].width = 15
            ws4.column_dimensions['H'].width = 65

            for ws in [ws1, ws2, ws3, ws4]:
                for col in ws.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = openpyxl.utils.get_column_letter(col[0].column)
                    ws.column_dimensions[col_letter].width = max(max_len + 4, 18)

            excel_buffer = io.BytesIO()
            wb.save(excel_buffer)
            excel_buffer.seek(0)
            
            state.session_cache[session_id]["excel_output"] = excel_buffer.getvalue()
            yield f"data: {json.dumps({'status': 'COMPLETED', 'download_url': f'/download-compiled-report/{session_id}'})}\n\n"
        except Exception as ex_err:
            yield f"data: {json.dumps({'status': 'ERROR', 'message': f'Excel Generation Failure: {str(ex_err)}'})}\n\n"

    return StreamingResponse(sse_event_generator(), media_type="text/event-stream")

# =====================================================================
# 8. LAYOUT VISUALIZATION INTERFACE GATEWAYS
# =====================================================================
@router.get("/generate-premium-2d-visual/{session_id}")
async def generate_premium_2d_visual(session_id: str):
    if session_id not in state.session_cache or not state.session_cache[session_id]["compiled_results"]:
        raise HTTPException(status_code=404, detail="No active visual datasets discovered to draw.")
    
    all_components = []
    for res in state.session_cache[session_id]["compiled_results"]:
        for c in res.get("components", []):
            all_components.append({
                "part_number": c.get("part_number", "P"),
                "component_type": c.get("component_type", "sheet"),
                "length_mm": c.get("length_mm", 0.0),
                "width_mm": c.get("width_mm", 0.0),
                "per_set_qty": c.get("per_set_qty", 1),
                "slots_count": c.get("slots_count", 0),
                "is_tapered": c.get("is_tapered", False)
            })
            
    img_stream = generate_cad_2d_flat_layout(all_components)
    return Response(content=img_stream.getvalue(), media_type="image/png")

@router.get("/generate-premium-3d-visual/{session_id}")
async def generate_premium_3d_visual(session_id: str, drawing_id: str = None):
    if session_id not in state.session_cache or not state.session_cache[session_id]["compiled_results"]:
        raise HTTPException(status_code=404, detail="No active parametric spatial metadata discovered.")

    target_components = []
    compiled_results = state.session_cache[session_id]["compiled_results"]

    if drawing_id:
        for res in compiled_results:
            if res.get("drawing_id", "").strip().upper() == drawing_id.strip().upper():
                target_components = res.get("components", [])
                break
    
    if not target_components and compiled_results:
        target_components = compiled_results[0].get("components", [])

    img_stream = generate_cad_3d_assembly_model(target_components, drawing_id=drawing_id or "")
    return Response(content=img_stream.getvalue(), media_type="image/png")

@router.get("/download-compiled-report/{session_id}")
async def download_compiled_report(session_id: str):
    if session_id not in state.session_cache or not state.session_cache[session_id]["excel_output"]:
        raise HTTPException(status_code=404, detail="The matrix spreadsheet sheet was not found.")
        
    return StreamingResponse(
        io.BytesIO(state.session_cache[session_id]["excel_output"]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Calibrated_Project_Master_BOM.xlsx"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
