# Backend Flow Notes

## Current Costing Flow

Upload/batch endpoints receive drawings and store the files in a session cache.

AI extraction reads each uploaded drawing and returns BOM components with part number, component type, dimensions, quantity, references, and drawing-level weight.

The extraction layer now also calculates a `blank_required` object for every BOM component. This tells the backend what raw blank or stock is required to make that part.

For sheet/plate parts, `blank_required` uses the part length, width, and thickness against a standard `2500 x 1250 mm` sheet. It calculates blank weight, parts per sheet, allocated stock weight per part, blank scrap per part, and yield percentage.

For rod/profile parts, `blank_required` uses the part length against a standard `6000 mm` rod/profile. It calculates pieces per rod, leftover length, allocated stock weight per part, blank scrap per part, and yield percentage.

Excel export reads each component and writes the blank/stock details into `All Components Ledger` and `Nesting Optimization Strategy`.

## Main Files

`main.py` starts the FastAPI app through the split app entrypoint.

`app/main.py` creates the FastAPI app and registers routers.

`app/ai/extraction.py` calls Gemini/Vertex AI and builds calibrated component data, including per-part blank/stock requirement.

`app/routes/batch.py` handles upload sessions, async processing, and Excel export.

`app/services/costing.py` contains costing formulas for process and geometry values.

`app/services/visuals.py` creates visual layout images.
