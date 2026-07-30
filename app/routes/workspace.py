from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def serve_frontend_workspace():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Premium Multi-Assembly Costing Matrix</title>
        <link href="https://fonts.googleapis.com/css2?family=Segoe+UI:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; }
            body { background-color: #f4f6f9; color: #333; padding: 30px; display: flex; justify-content: center; }
            .workspace { max-width: 950px; width: 100%; background: #ffffff; padding: 35px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.06); }
            h1 { color: #1B365D; font-size: 26px; text-align: center; margin-bottom: 6px; }
            p.info { text-align: center; color: #666; font-size: 14px; margin-bottom: 25px; }
            .drop-zone { border: 2px dashed #1B365D; background: #f8faff; border-radius: 8px; padding: 35px; text-align: center; cursor: pointer; transition: 0.2s; }
            .drop-zone:hover { background: #f1f5fc; }
            .drop-zone p { font-weight: 600; color: #1B365D; }
            #file-picker { display: none; }
            .btn-action { width: 100%; background: #1B365D; color: white; padding: 14px; border: none; border-radius: 6px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 20px; transition: 0.2s; }
            .btn-action:hover { background: #122540; }
            .btn-action:disabled { background: #ccc; cursor: not-allowed; }
            .monitor-panel { margin-top: 30px; display: none; }
            .monitor-title { font-size: 15px; font-weight: 700; color: #1B365D; margin-bottom: 12px; display: flex; justify-content: space-between; }
            .progress-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            .progress-table th, .progress-table td { border: 1px solid #e0e0e0; padding: 10px 12px; text-align: left; font-size: 13.5px; }
            .progress-table th { background: #f4f6f9; color: #1B365D; font-weight: 600; }
            .badge-success { background: #d4edda; color: #155724; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 12px; }
            .popup-alert { display: none; background: #d4edda; border: 1px solid #c3e6cb; color: #155724; padding: 15px; border-radius: 6px; font-weight: 600; margin-top: 20px; text-align: center; }
            .btn-download { display: none; width: 100%; background: #28a745; color: white; padding: 14px; border: none; border-radius: 6px; font-size: 16px; font-weight: 700; cursor: pointer; margin-top: 15px; text-align: center; text-decoration: none; }
            
            .visual-buttons-container { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 15px; }
            .btn-visual { flex: 1; min-width: 200px; display: none; text-align: center; padding: 14px; border-radius: 6px; font-size: 14px; font-weight: 700; color: white; border: none; cursor: pointer; text-decoration: none; }
            #btn-2d-trigger { background: #4A90E2; }
            #btn-3d-trigger { background: #6f42c1; }
            .btn-dl-lnk { background: #218838 !important; }
            
            .visualizer-frame-dock { display: flex; flex-direction: column; gap: 20px; margin-top: 20px; }
            .visualizer-container { display: none; text-align: center; border: 1px solid #e0e0e0; padding: 15px; border-radius: 8px; background: #fafafa; }
            .visualizer-container img { max-width: 100%; max-height: 75vh; width: auto; height: auto; border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); object-fit: contain; }
            
            .drawing-selector-bar { display: none; margin-top: 15px; gap: 8px; justify-content: center; flex-wrap: wrap; }
            .drawing-tab { padding: 8px 16px; background: #e2e8f0; border: none; border-radius: 4px; font-weight: 600; cursor: pointer; color: #1B365D; font-size: 13px; }
            .drawing-tab.active { background: #6f42c1; color: white; }
        </style>
    </head>
    <body>
        <div class="workspace">
            <h1>Industrial Costing & Nesting Yield Matrix</h1>
            <p class="info">Advanced Verification Workspace & Simulation Engine (True Engineering Isometric View)</p>
            
            <div class="drop-zone" id="drop-box">
                <p>Click or Drag Blueprints and Batch Zips Here</p>
                <input type="file" id="file-picker" multiple>
            </div>
            
            <button class="btn-action" id="upload-trigger" disabled>Initialize Drawing Processing</button>
            
            <div class="popup-alert" id="success-banner">🎉 Verification complete. Asset models and commercial values successfully calibrated.</div>
            <a class="btn-download" id="download-trigger" href="#">Download Value Matrix Worksheet (.xlsx)</a>
            
            <div class="visual-buttons-container">
                <button class="btn-visual" id="btn-2d-trigger">View Aggregated 2D Layout Map</button>
                <a class="btn-visual btn-dl-lnk" id="lnk-2d-download" download="aggregated_2d_nesting_layout.png" href="#">Download 2D Image PNG</a>
                <button class="btn-visual" id="btn-3d-trigger">View 3D CAD Isometric Digital Twin</button>
                <a class="btn-visual btn-dl-lnk" id="lnk-3d-download" download="isometric_3d_model.png" href="#">Download 3D Isometric PNG</a>
            </div>

            <div class="drawing-selector-bar" id="drawing-selector-bar">
                <span style="font-weight:600; align-self:center; font-size:13px; color:#4a5568;">Select Drawing Target:</span>
            </div>
            
            <div class="visualizer-frame-dock">
                <div class="visualizer-container" id="visualizer-frame-2d">
                    <h3 style="color:#1B365D; margin-bottom:10px; font-size:14px;">Aggregated 2D Manufacturing Yield & Punched Hole Optimization Layer</h3>
                    <img id="2d-image-display" src="" alt="2D Layout View">
                </div>
                <div class="visualizer-container" id="visualizer-frame-3d">
                    <h3 style="color:#6f42c1; margin-bottom:10px; font-size:14px;" id="3d-header-title">True Engineering Isometric 3D CAD Digital Twin</h3>
                    <img id="3d-image-display" src="" alt="3D Model View">
                </div>
            </div>

            <div class="monitor-panel" id="monitor-panel">
                <div class="monitor-title">
                    <span>Asynchronous Execution Diagnostics</span>
                    <span style="color:#666; font-weight:400;" id="running-status">Awaiting batch initialization...</span>
                </div>
                <table class="progress-table">
                    <thead>
                        <tr>
                            <th>Drawing Reference</th>
                            <th>Target Plan Mass</th>
                            <th>Calibrated Mass Output</th>
                            <th>Status Matrix</th>
                        </tr>
                    </thead>
                    <tbody id="progress-rows"></tbody>
                </table>
            </div>
        </div>

        <script>
            const dropBox = document.getElementById('drop-box');
            const filePicker = document.getElementById('file-picker');
            const uploadTrigger = document.getElementById('upload-trigger');
            const monitorPanel = document.getElementById('monitor-panel');
            const progressRows = document.getElementById('progress-rows');
            const runningStatus = document.getElementById('running-status');
            const successBanner = document.getElementById('success-banner');
            const downloadTrigger = document.getElementById('download-trigger');
            const btn2DTrigger = document.getElementById('btn-2d-trigger');
            const btn3DTrigger = document.getElementById('btn-3d-trigger');
            const lnk2DDownload = document.getElementById('lnk-2d-download');
            const lnk3DDownload = document.getElementById('lnk-3d-download');
            const frame2D = document.getElementById('visualizer-frame-2d');
            const frame3D = document.getElementById('visualizer-frame-3d');
            const img2DDisplay = document.getElementById('2d-image-display');
            const img3DDisplay = document.getElementById('3d-image-display');
            const drawingSelectorBar = document.getElementById('drawing-selector-bar');
            const header3DTitle = document.getElementById('3d-header-title');
            
            let uploadedFilesStash = [];
            let activeSessionId = "";
            let processedDrawingIds = [];
            let currentSelectedDrawingId = "";

            dropBox.addEventListener('click', () => filePicker.click());
            filePicker.addEventListener('change', (e) => storeFiles(e.target.files));
            dropBox.addEventListener('dragover', (e) => { e.preventDefault(); dropBox.style.background = '#eef3fc'; });
            dropBox.addEventListener('dragleave', () => { dropBox.style.background = '#f8faff'; });
            dropBox.addEventListener('drop', (e) => {
                e.preventDefault();
                storeFiles(e.dataTransfer.files);
            });

            function storeFiles(files) {
                uploadedFilesStash = Array.from(files);
                if(uploadedFilesStash.length > 0) {
                    dropBox.querySelector('p').innerText = uploadedFilesStash.length + " Blueprints Configured";
                    uploadTrigger.disabled = false;
                }
            }

            uploadTrigger.addEventListener('click', async () => {
                uploadTrigger.disabled = true;
                uploadTrigger.style.display = 'none';
                monitorPanel.style.display = 'block';
                progressRows.innerHTML = '';
                runningStatus.innerText = "Processing drawing geometries...";
                
                const dataPayload = new FormData();
                uploadedFilesStash.forEach(f => dataPayload.append('raw_files', f));

                const handshake = await fetch('/initialize-async-batch', { method: 'POST', body: dataPayload });
                const session = await handshake.json();
                activeSessionId = session.session_id;
                
                const eventConnection = new EventSource('/stream-live-calculations/' + activeSessionId);
                
                eventConnection.onmessage = function(event) {
                    const dataPacket = JSON.parse(event.data);
                    
                    if (dataPacket.status === 'PROGRESS') {
                        processedDrawingIds.push(dataPacket.drawing_id);
                        const tr = document.createElement('tr');
                        tr.innerHTML = '<td>' + dataPacket.drawing_id + '</td><td>' + dataPacket.target_weight + ' kg</td><td style="color:#28a745; font-weight:600;">' + dataPacket.calibrated_weight + ' kg</td><td><span class="badge-success">Verified</span></td>';
                        progressRows.appendChild(tr);
                    } 
                    else if (dataPacket.status === 'ERROR') {
                        eventConnection.close();
                        runningStatus.innerText = "Execution Interrupted: " + dataPacket.message;
                    }
                    else if (dataPacket.status === 'COMPLETED') {
                        eventConnection.close();
                        runningStatus.innerText = "Calculations Completed Successfully.";
                        successBanner.style.display = 'block';
                        downloadTrigger.href = dataPacket.download_url;
                        downloadTrigger.style.display = 'block';
                        
                        btn2DTrigger.style.display = 'block';
                        btn3DTrigger.style.display = 'block';
                        
                        lnk2DDownload.href = '/generate-premium-2d-visual/' + activeSessionId;
                        lnk2DDownload.style.display = 'block';
                        
                        buildDrawingTabs();
                    }
                };
            });

            function buildDrawingTabs() {
                drawingSelectorBar.innerHTML = '<span style="font-weight:600; align-self:center; font-size:13px; color:#4a5568;">Select Drawing Target:</span>';
                processedDrawingIds.forEach((drgId, idx) => {
                    const tabBtn = document.createElement('button');
                    tabBtn.className = 'drawing-tab' + (idx === 0 ? ' active' : '');
                    tabBtn.innerText = drgId;
                    tabBtn.onclick = () => selectDrawingTarget(drgId, tabBtn);
                    drawingSelectorBar.appendChild(tabBtn);
                });
                if(processedDrawingIds.length > 0) {
                    currentSelectedDrawingId = processedDrawingIds[0];
                }
            }

            function selectDrawingTarget(drgId, btnElement) {
                currentSelectedDrawingId = drgId;
                document.querySelectorAll('.drawing-tab').forEach(b => b.classList.remove('active'));
                btnElement.classList.add('active');
                refresh3DView();
            }

            function refresh3DView() {
                if(currentSelectedDrawingId) {
                    header3DTitle.innerText = "True Engineering Isometric 3D CAD: " + currentSelectedDrawingId;
                    img3DDisplay.src = '/generate-premium-3d-visual/' + activeSessionId + '?drawing_id=' + encodeURIComponent(currentSelectedDrawingId) + '&t=' + new Date().getTime();
                    lnk3DDownload.href = '/generate-premium-3d-visual/' + activeSessionId + '?drawing_id=' + encodeURIComponent(currentSelectedDrawingId);
                }
            }

            btn2DTrigger.addEventListener('click', () => {
                img2DDisplay.src = '/generate-premium-2d-visual/' + activeSessionId + '?t=' + new Date().getTime();
                frame2D.style.display = 'block';
            });

            btn3DTrigger.addEventListener('click', () => {
                drawingSelectorBar.style.display = 'flex';
                refresh3DView();
                frame3D.style.display = 'block';
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# =====================================================================
# 7. TRANSACTION EXPORT HANDLERS
# =====================================================================
