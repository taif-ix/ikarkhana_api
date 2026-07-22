# Cost Estimator Developer Guide

This document explains the backend flow and folder structure in simple language so new developers can understand the project quickly.

## What This Project Does

This is the FastAPI backend for the ikarkhana diagram costing POC.

The user uploads one or more engineering drawings. The backend:

1. Converts uploaded TIFF/image files into browser-friendly PNG previews.
2. Sends drawings to Gemini or Vertex AI for extraction.
3. Extracts part names, dimensions, material, child drawing references, and manufacturing features.
4. Calculates weight, scrap, material cost, laser/press/bending/welding/painting cost.
5. Returns structured JSON for the frontend to show tables, part cards, breakdowns, and Excel reports.

The frontend is separate:

```text
https://github.com/taif-ix/ikarkhana_web
```

This repo should stay backend/API only.

## Main Runtime Flow

### Single Drawing Flow

1. Frontend uploads a drawing.
2. Backend converts preview image if needed.
3. Frontend calls extraction/cost endpoints.
4. Gemini reads the drawing and returns structured JSON.
5. Python formulas calculate weight, scrap, and cost.
6. Frontend displays extracted fields, part cards, formula breakdown, and totals.

Main endpoints:

```text
POST /expand-upload
POST /extract-dimensions
POST /extract-structured
POST /extract-cost-breakdown
POST /estimate
```

### Batch Drawing Flow

1. User uploads multiple files or a ZIP.
2. Backend expands ZIP files and converts previews to PNG.
3. Frontend sends all files to backend batch processing.
4. Backend creates an in-memory batch job.
5. FastAPI starts async workers using `asyncio`.
6. Each file is processed independently.
7. Frontend polls job status.
8. Processed files can be opened one by one.
9. Failed files can be retried individually.

Main endpoints:

```text
POST /batch-process/start
GET /batch-process/{job_id}
POST /batch-process/{job_id}/retry
POST /batch-extract-references
```

Important: current batch jobs are stored in memory. If the server restarts, active jobs are lost. For production, use Redis, Celery/RQ, Cloud Tasks, or a database-backed job table.

## Folder Structure

```text
app/
  main.py
  api/
    routes/
      __init__.py
      common.py
      system/
      uploads/
      batch/
      extraction/
      costing/
  core/
    config.py
  models/
    schemas.py
  services/
    vision.py
    estimator.py
    formulas.py
```

## File And Folder Purpose

### `app/main.py`

FastAPI application entry point.

It:

- Loads `.env` values.
- Creates the FastAPI app.
- Enables CORS.
- Includes all API routes.

Run locally with:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

### `app/api/routes/__init__.py`

Main route aggregator.

It imports route groups from subfolders and combines them into one router:

- system routes
- upload routes
- batch routes
- extraction routes
- costing routes

`app/main.py` imports this as:

```python
from app.api.routes import router
```

### `app/api/routes/common.py`

Shared route helper functions and shared request models.

It contains:

- allowed upload extensions
- data URL helper
- drawing filename matching helpers
- child drawing reader
- shared structured costing request schema

### `app/api/routes/system/routes.py`

Basic API/system endpoints.

Endpoints:

```text
GET /
GET /health
GET /gemini-config
GET /vertex-config
```

Use this file for health checks, metadata, and config visibility.

### `app/api/routes/uploads/routes.py`

Upload and preview endpoints.

Endpoints:

```text
POST /expand-upload
POST /diagram-preview
```

Responsibilities:

- Accept single files, multiple files, or ZIP files.
- Expand ZIP files.
- Convert TIFF/JPG/PNG drawings into optimized PNG previews.
- Return preview images as base64 data URLs for the frontend.

This prevents blank TIFF previews in the browser.

### `app/api/routes/batch/routes.py`

Batch processing endpoints and async worker logic.

Endpoints:

```text
POST /batch-process/start
GET /batch-process/{job_id}
POST /batch-process/{job_id}/retry
POST /batch-extract-references
```

Responsibilities:

- Create batch jobs.
- Store files in memory for the POC.
- Process drawings with `asyncio` workers.
- Attach uploaded child files where filenames match detected references.
- Retry failed files one by one.
- Return job status for frontend polling.

Concurrency is controlled by:

```text
BATCH_PROCESS_CONCURRENCY
```

Default value is `2`.

### `app/api/routes/extraction/routes.py`

Gemini/Vertex extraction endpoints.

Endpoints:

```text
POST /extract-dimensions
POST /extract-references
POST /extract-structured
POST /extract-cost-breakdown
```

Responsibilities:

- Send drawings to Gemini/Vertex.
- Extract dimensions.
- Extract child drawing references.
- Extract structured part-wise JSON.
- Optionally calculate cost directly from extracted structured JSON.

### `app/api/routes/costing/routes.py`

Cost calculation endpoints.

Endpoints:

```text
POST /calculate-cost-breakdown
POST /estimate
```

Responsibilities:

- Calculate cost from already-extracted structured JSON.
- Support older/manual estimate flow.
- Call Python formula engine.

### `app/core/config.py`

Configuration and default rates.

Contains:

- material rates
- process rates
- stock sizes
- Gemini model allow-list
- `.env` loading helper
- material density/code mapping

Examples:

```text
MS = 60 INR/kg
SS = 240 INR/kg
Aluminium = 200 INR/kg
Rod/profile stock = 6000 mm
Sheet stock = 2500 x 1250 mm
```

### `app/models/schemas.py`

Pydantic request/response models.

This file defines the JSON shapes used by the API.

Important models:

- `ExtractedDimensions`
- `StructuredExtraction`
- `StructuredCostBreakdown`
- `CostedPartBreakdown`
- `CalculationStep`
- `EstimateResponse`
- `ReferenceExtraction`
- `BatchReferenceExtraction`

If frontend JSON shape changes, this is usually one of the first files to check.

### `app/services/vision.py`

Gemini/Vertex AI integration and image preprocessing.

Responsibilities:

- Convert and optimize images before Gemini.
- Convert TIFF to PNG.
- Build prompts for:
  - dimension extraction
  - structured extraction
  - reference extraction
- Call Gemini API or Vertex AI.
- Clean/repair Gemini JSON responses.

Important functions:

```python
image_bytes_for_preview()
image_bytes_for_gemini()
extract_dimensions_with_gemini()
extract_references_with_gemini()
extract_structured_with_gemini()
```

### `app/services/estimator.py`

Main costing engine.

Responsibilities:

- Convert extracted part dimensions into weight.
- Calculate gross raw material weight.
- Calculate scrap/offcut.
- Deduct scrap resale value from gross material cost.
- Calculate laser, press, bending, welding, painting, and tacking costs.
- Build part-wise cost breakdown.
- Build formula trail for UI breakdown popups.

Important function:

```python
calculate_structured_cost_breakdown()
```

### `app/services/formulas.py`

Reusable formula helpers.

Contains:

- square tube weight
- round tube weight
- plate weight
- rod weight
- sheet nesting estimate
- rod stock nesting estimate
- formatting helpers for INR/kg/mm/m2

Use this file when adding or updating formulas.

## Environment Variables

For Gemini API:

```powershell
$env:GEMINI_PROVIDER="gemini_api"
$env:GEMINI_API_KEY="your-api-key"
$env:GEMINI_MODEL="gemini-3.5-flash"
```

For Vertex AI:

```powershell
$env:GEMINI_PROVIDER="vertex_ai"
$env:GOOGLE_CLOUD_PROJECT="your-project-id"
$env:GOOGLE_CLOUD_LOCATION="asia-south1"
$env:GEMINI_MODEL="gemini-2.5-pro"
```

For batch concurrency:

```powershell
$env:BATCH_PROCESS_CONCURRENCY="2"
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

Check:

```text
http://127.0.0.1:8010/health
http://127.0.0.1:8010/docs
```

## How To Add A New API

1. Decide the route group:
   - upload related: `app/api/routes/uploads/routes.py`
   - Gemini extraction: `app/api/routes/extraction/routes.py`
   - costing: `app/api/routes/costing/routes.py`
   - batch: `app/api/routes/batch/routes.py`
   - system/config: `app/api/routes/system/routes.py`

2. Add or reuse Pydantic models in:

```text
app/models/schemas.py
```

3. Put business logic in services, not directly in route files:

```text
app/services/
```

4. Validate:

```powershell
python -m compileall app
```

## Developer Notes

- Route files should stay thin.
- Formula changes should mostly go in `services/formulas.py` or `services/estimator.py`.
- Gemini prompt changes should go in `services/vision.py`.
- API response shape changes should be reflected in `models/schemas.py`.
- Do not put frontend code in this backend repo.
- Active batch jobs are in memory for now; production needs persistent job storage.

