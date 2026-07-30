import io
from typing import Any, Dict, List

import numpy as np
from PIL import Image
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle
import pyvista as pv

pv.OFF_SCREEN = True


# 2D nesting and 3D assembly visual generation helpers.
# =====================================================================
def generate_cad_2d_flat_layout(all_aggregated_components: List[Dict[str, Any]]) -> io.BytesIO:
    fig = Figure(figsize=(12, 11))
    
    ax1 = fig.add_subplot(211)
    ax1.set_title("Aggregated Multi-Drawing 2D Nesting & Yield Map (Standard 2500x1250mm Plate Stock)", fontsize=11, fontweight='bold', color='#1B365D')
    
    stock_plate = Rectangle((0, 0), 2500, 1250, edgecolor='#1B365D', facecolor='#F8F9FA', linestyle='--', linewidth=1.5)
    ax1.add_patch(stock_plate)
    
    current_x, current_y = 60, 60
    max_row_height = 0
    has_flat_parts = False
    
    for idx, c in enumerate(all_aggregated_components):
        c_type = c.get("component_type", "sheet").lower()
        L = float(c.get("length_mm", 0.0))
        W = float(c.get("width_mm", 0.0))
        qty = int(c.get("per_set_qty", 1))
        slots = int(c.get("slots_count", 0))
        is_tapered = c.get("is_tapered", False)
        part_no = str(c.get("part_number", str(idx)))
        
        if c_type in ["perforated_tray", "tapered_gusset", "sheet", "handle"] and L > 0 and W > 0:
            has_flat_parts = True
            for _ in range(min(qty, 3)):
                if current_x + L > 2420:
                    current_x = 60
                    current_y += max_row_height + 50
                    max_row_height = 0
                if current_y + W > 1180:
                    break
                
                if c_type == "tapered_gusset" or is_tapered:
                    vertices = np.array([
                        [current_x, current_y],
                        [current_x + L, current_y],
                        [current_x + L * 0.4, current_y + W],
                        [current_x, current_y + W * 0.7]
                    ])
                    poly = Polygon(vertices, edgecolor='#2980B9', facecolor='#AED6F1', alpha=0.85, linewidth=1)
                    ax1.add_patch(poly)
                    ax1.text(current_x + L*0.35, current_y + W*0.35, f"Gusset {part_no}\n{L:.0f}x{W:.0f}", color='#2C3E50', weight='bold', fontsize=7, ha='center')
                else:
                    part_rect = Rectangle((current_x, current_y), L, W, edgecolor='#2C3E50', facecolor='#BDC3C7', alpha=0.9, linewidth=1)
                    ax1.add_patch(part_rect)
                    
                    if slots > 0:
                        slot_step_x = max(15.0, L / 10.0)
                        slot_step_y = max(10.0, W / 4.0)
                        sx = current_x + 10
                        while sx < current_x + L - 10:
                            sy = current_y + 10
                            while sy < current_y + W - 10:
                                slot_elem = Rectangle((sx, sy), slot_step_x * 0.4, slot_step_y * 0.5, facecolor='#FFFFFF', edgecolor='#7F8C8D', linewidth=0.3)
                                ax1.add_patch(slot_elem)
                                sy += slot_step_y
                            sx += slot_step_x
                    
                    ax1.text(current_x + L/2, current_y + W/2, f"P-{part_no}\n{L:.0f}x{W:.0f}", color='#1B365D', weight='bold', fontsize=7.5, ha='center', va='center', bbox=dict(facecolor='white', alpha=0.85, boxstyle='round,pad=0.2'))
                
                max_row_height = max(max_row_height, W)
                current_x += L + 40  
                
    if not has_flat_parts:
        ax1.text(1250, 625, "No flat structural or perforated profile layouts identified.", ha='center', va='center', color='#888888', style='italic')

    ax1.set_xlim(-50, 2550)
    ax1.set_ylim(-50, 1300)
    ax1.set_xlabel("X Dimension Vector Bounds (mm)", fontsize=9, fontweight='bold')
    ax1.set_ylabel("Y Dimension Vector Bounds (mm)", fontsize=9, fontweight='bold')
    ax1.grid(True, linestyle=':', alpha=0.4)

    ax2 = fig.add_subplot(212)
    ax2.set_title("Aggregated Linear Profile Stock Run Configurations (6000mm Stock Tracks)", fontsize=11, fontweight='bold', color='#1B365D')
    
    stock_track = Rectangle((0, 10), 6000, 30, edgecolor='#7F8C8D', facecolor='#F4F6F9', linestyle='-', linewidth=1.2)
    ax2.add_patch(stock_track)
    
    track_cursor = 50.0
    rendered_tubes = False
    for c in all_aggregated_components:
        if c.get("component_type", "").lower() == "tube":
            t_len = float(c.get("length_mm", 500.0))
            t_qty = int(c.get("per_set_qty", 1))
            for q_i in range(min(t_qty, 3)):
                if track_cursor + t_len < 5950:
                    rendered_tubes = True
                    tube_block = Rectangle((track_cursor, 12), t_len, 26, edgecolor='#1B365D', facecolor='#5D6D7E', alpha=0.85)
                    ax2.add_patch(tube_block)
                    ax2.text(track_cursor + t_len/2, 25, f"Tube Cut: {t_len:.0f}mm", color='white', fontsize=7.5, weight='bold', ha='center', va='center')
                    track_cursor += t_len + 30
                    
    if not rendered_tubes:
        ax2.text(3000, 25, "Standard 6000mm Stock Feed Ready", ha='center', va='center', color='#7F8C8D', style='italic')

    ax2.set_xlim(0, 6100)
    ax2.set_ylim(0, 50)
    ax2.set_xlabel("Linear Stock Length Vector (mm)", fontsize=9, fontweight='bold')
    ax2.get_yaxis().set_visible(False)
    ax2.grid(True, linestyle=':', alpha=0.4)
    
    fig.tight_layout(pad=3.0)
    img_buffer = io.BytesIO()
    fig.savefig(img_buffer, format='png', dpi=140, bbox_inches='tight')
    img_buffer.seek(0)
    return img_buffer

def determine_side_by_length(drawing_components: List[Dict[str, Any]]) -> str:
    total_len_left = 0.0
    total_len_right = 0.0
    
    for c in drawing_components:
        length = float(c.get("length_mm", 0.0))
        cutting_len = float(c.get("laser_cutting_length_mm", 0.0))
        desc = str(c.get("description", "")).upper()
        
        if "LH" in desc or "LEFT" in desc:
            total_len_left += (length + cutting_len)
        elif "RH" in desc or "RIGHT" in desc:
            total_len_right += (length + cutting_len)
        else:
            total_len_right += length

    if total_len_left > total_len_right:
        return "left"
    else:
        return "right"

def generate_cad_3d_assembly_model(drawing_components: List[Dict[str, Any]], drawing_id: str = "") -> io.BytesIO:
    plotter = pv.Plotter(off_screen=True, window_size=[1200, 1600])
    plotter.set_background("#EAECEE") 

    is_column = any("tube" in str(c.get("component_type", "")).lower() or "column" in str(c.get("part_number", "")).lower() for c in drawing_components)

    if is_column:
        col_width = 45.0  # Square tube 45x45 mm profile width
        tube_length = 2581.0  
        
        # Dynamic extraction of horizontal outreach from current drawing components
        base_arm_length = 437.0  
        for c in drawing_components:
            desc = str(c.get("description", "")).upper()
            c_type = str(c.get("component_type", "")).upper()
            if "CHAIR" in desc or "GUSSET" in c_type or "ANGLE" in desc:
                w_val = float(c.get("length_mm", 0.0)) or float(c.get("width_mm", 0.0))
                if 50.0 < w_val < 1500.0:
                    base_arm_length = w_val
                    break
        
        # Exact vertical dimensions from blueprint image_3b045b.png
        base_z_start = 131.0       # Exact gap from bottom base plate to starting point of chair angle
        chair_angle_top_z = 305.0  # Exact total height from bottom plate to top of chair angle
        bracket_height = chair_angle_top_z - base_z_start  # 174.0 mm vertical span
        
        handle_len = 200.0
        handle_width = 70.0
        handle_thick = 2.0
        
        has_lh = False
        has_rh = False
        explicit_tag_found = False
        
        for c in drawing_components:
            desc = str(c.get("description", "")).upper()
            p_num = str(c.get("part_number", "")).upper()
            c_type = str(c.get("component_type", "")).upper()
            
            if "TUBE" in c_type:
                l_val = float(c.get("length_mm", 0.0))
                if l_val > 2000.0:
                    tube_length = l_val

            if "HANDLE" in desc or "HANDLE" in c_type:
                l_val = float(c.get("length_mm", 0.0))
                if l_val > 50.0:
                    handle_len = l_val
                w_val = float(c.get("width_mm", 0.0))
                if w_val > 10.0:
                    handle_width = w_val
                t_val = float(c.get("thickness_mm", 0.0))
                if t_val > 0.0:
                    handle_thick = t_val

            if "-LH" in p_num or "-LH" in desc or "CHAIR ANGLE-LH" in desc:
                has_lh = True
                explicit_tag_found = True
            if "-RH" in p_num or "-RH" in desc or "CHAIR ANGLE-RH" in desc:
                has_rh = True
                explicit_tag_found = True

        if not explicit_tag_found:
            preferred_side = determine_side_by_length(drawing_components)
            if preferred_side == "left":
                has_lh = True
                has_rh = False
            else:
                has_lh = False
                has_rh = True

        satin_steel = dict(pbr=True, metallic=0.50, roughness=0.42, color="#D8E2EC", smooth_shading=False)
        hardware_steel = dict(pbr=True, metallic=0.85, roughness=0.20, color="#2C3E50", smooth_shading=True)

        # 1. Base Mounting Plate
        base_plate = pv.Box(bounds=(-55.0, 55.0, -75.0, 75.0, 0.0, 5.0))
        plotter.add_mesh(base_plate, **satin_steel)

        # 2. Main Square Tube (Strictly sharp-edged square profile)
        column = pv.Box(bounds=(-col_width/2, col_width/2, -col_width/2, col_width/2, 5.0, 5.0 + tube_length))
        plotter.add_mesh(column, **satin_steel)

        # 3. Top Plate
        top_plate = pv.Box(bounds=(-62.5, 62.5, -62.5, 62.5, 5.0 + tube_length, 5.0 + tube_length + 5.0))
        plotter.add_mesh(top_plate, **satin_steel)

        # 4. Chair Angles (Mounted with exact dynamically extracted outreach length and vertical datums)
        base_arm_h_tip = 45.0
        base_arm_w = 45.0

        active_directions = []
        if has_rh: active_directions.append(1)   
        if has_lh: active_directions.append(-1)  

        for x_dir in active_directions:
            inner_x = (col_width / 2) * x_dir
            outer_x = (col_width / 2 + base_arm_length) * x_dir
            
            pts = np.array([
                [inner_x, 0, base_z_start],
                [outer_x, 0, base_z_start + bracket_height - base_arm_h_tip],
                [outer_x, 0, base_z_start + bracket_height],
                [inner_x, 0, base_z_start + bracket_height]
            ])

            profile_face = pv.PolyData(pts, np.array([4, 0, 1, 2, 3]))
            arm_mesh = profile_face.extrude((0, base_arm_w, 0), capping=True)
            arm_mesh.translate((0, -base_arm_w/2, 0), inplace=True)
            plotter.add_mesh(arm_mesh, **satin_steel)

            for frac in [0.35, 0.75]:
                hx = inner_x + (base_arm_length * frac * x_dir)
                hole = pv.Cylinder(center=(hx, 0, base_z_start + bracket_height + 0.5), direction=(0,0,1), radius=4.5, height=2.0, resolution=20)
                plotter.add_mesh(hole, color="#1A1A1A", smooth_shading=True)

        # 5. Visible Protruding Screwing Pieces / Hardware (Item 6)
        z_holes = [tube_length * 0.3, tube_length * 0.38, tube_length * 0.64, tube_length * 0.72]
        for z in z_holes:
            boss = pv.Cylinder(center=(0.0, -col_width/2 - 4.0, z), direction=(0, 1, 0), radius=9.0, height=8.0, resolution=30)
            plotter.add_mesh(boss, color="#A0ABB5", pbr=True, metallic=0.6, roughness=0.3)
            bolt_head = pv.Cylinder(center=(0.0, -col_width/2 - 8.0, z), direction=(0, 1, 0), radius=5.5, height=4.0, resolution=20)
            plotter.add_mesh(bolt_head, **hardware_steel)

        # 6. Industrial C-Shaped Handle Welded to Side Face
        handle_z_center = tube_length * 0.65 
        side_mult = 1.0 if has_rh and not has_lh else -1.0
        h_outreach = 60.0
        h_dia = 12.0

        standoff_bot = pv.Cylinder(center=(0.0, (-col_width/2 - h_outreach/2) * side_mult, handle_z_center - handle_len/2), direction=(0, side_mult, 0), radius=h_dia/2, height=h_outreach, resolution=20)
        standoff_top = pv.Cylinder(center=(0.0, (-col_width/2 - h_outreach/2) * side_mult, handle_z_center + handle_len/2), direction=(0, side_mult, 0), radius=h_dia/2, height=h_outreach, resolution=20)
        grip_bar = pv.Cylinder(center=(0.0, (-col_width/2 - h_outreach) * side_mult, handle_z_center), direction=(0, 0, 1), radius=h_dia/2, height=handle_len, resolution=20)

        plotter.add_mesh(standoff_bot, color="#2C3E50", pbr=True, metallic=0.7, roughness=0.4)
        plotter.add_mesh(standoff_top, color="#2C3E50", pbr=True, metallic=0.7, roughness=0.4)
        plotter.add_mesh(grip_bar, color="#2C3E50", pbr=True, metallic=0.7, roughness=0.4)

    else:
        tray_length = 600.0
        tray_width = 200.0
        for c in drawing_components:
            if float(c.get("length_mm", 0.0)) > 0:
                tray_length = float(c.get("length_mm", 600.0))
            if float(c.get("width_mm", 0.0)) > 0:
                tray_width = float(c.get("width_mm", 200.0))

        tray_height = 30.0
        tray_mat = dict(pbr=True, metallic=0.7, roughness=0.4, color="#BDC3C7")

        tray_deck = pv.Box(bounds=(-tray_length/2, tray_length/2, -tray_width/2, tray_width/2, 0.0, tray_height))
        plotter.add_mesh(tray_deck, **tray_mat)
        left_flange = pv.Box(bounds=(-tray_length/2, tray_length/2, -tray_width/2 - 2.0, -tray_width/2, 0.0, tray_height))
        right_flange = pv.Box(bounds=(-tray_length/2, tray_length/2, tray_width/2, tray_width/2 + 2.0, 0.0, tray_height))
        plotter.add_mesh(left_flange, **tray_mat)
        plotter.add_mesh(right_flange, **tray_mat)

    plotter.enable_shadows()
    plotter.add_light(pv.Light(position=(3000, -3000, 3000), focal_point=(0, 0, 1300), intensity=1.2, color='white'))
    plotter.add_light(pv.Light(position=(-3000, 3000, 2000), focal_point=(0, 0, 1300), intensity=0.8, color='#FFFFFF'))
    plotter.add_light(pv.Light(position=(0, 4000, 2000), focal_point=(0, 0, 1300), intensity=0.6, color='#FFFFFF'))
    
    plotter.camera_position = 'iso'
    plotter.reset_camera()
    plotter.isometric_view()
    plotter.camera.zoom(1.12)

    img_array = plotter.screenshot(return_img=True)
    plotter.close()

    img = Image.fromarray(img_array)
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG', optimize=True)
    img_buffer.seek(0)
    return img_buffer
