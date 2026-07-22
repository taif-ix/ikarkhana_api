# ikarkhana Cost Estimator Backend Flow

This document explains the FastAPI backend in simple language so a new developer can understand the project flow and folder structure.

## 1. What This Backend Does

The backend receives engineering drawing files such as TIFF, PNG, PDF, or zip batches. It extracts drawing information using Gemini or Vertex AI, converts TIFF files to preview images, calculates material weight and costing, and returns structured JSON or Excel-ready costing data.

Main responsibilities:

- Accept uploaded engineering drawings.
- Convert uploaded TIFF files into browser-friendly PNG previews.
- Detect parent drawings and child/detail drawing references.
- Extract dimensions, materials, quantities, bends, cuts, welding, surface area, and costing inputs.
- Calculate part-wise and total cost in INR.
- Generate batch processing results and Excel export data.

## 2. High Level Flow

```text
User uploads drawing(s)
        |
        v
FastAPI upload route receives files
        |
        v
Preview service converts TIFF/PDF/image where needed
        |
        v
Vision service calls Gemini / Vertex AI for extraction
        |
        v
Pydantic schemas validate extracted JSON
        |
        v
Estimator service applies formulas
        |
        v
API returns dimensions, part-wise costing, totals, breakdowns, and export data
```

## 3. Important Backend Folders

### `app/`

Main Python application package.

### `app/main.py`

FastAPI application entry point.

It does these things:

- Creates the FastAPI app.
- Enables CORS so frontend can call the backend.
- Includes all API route groups from `app/api/routes`.

### `app/api/routes/`

Contains all route modules. This keeps API endpoints separated by feature instead of keeping everything in one large file.

### `app/api/routes/__init__.py`

Combines all route groups into one router.

`app/main.py` imports this router.

### `app/api/routes/common.py`

Common helper functions shared by route files.

Useful for:

- Reading uploaded files.
- Creating shared response helpers.
- Avoiding duplicate route utility code.

### `app/api/routes/system/routes.py`

System and configuration endpoints.

Typical endpoints:

- `/`
- `/health`
- `/gemini-config`
- `/vertex-config`

Used to check whether backend is running and whether Gemini / Vertex settings are configured.

### `app/api/routes/uploads/routes.py`

Upload and preview related endpoints.

Typical responsibility:

- Accept single drawing uploads.
- Generate preview images.
- Convert TIFF into PNG for browser display.
- Expand zip uploads if supported by the current frontend flow.

### `app/api/routes/extraction/routes.py`

Extraction endpoints.

Typical endpoints:

- `/extract-dimensions`
- `/extract-structured`
- `/extract-cost-breakdown`
- Reference scan endpoints if routed here.

This layer receives the uploaded file and calls the vision service.

### `app/api/routes/costing/routes.py`

Cost calculation endpoints.

Typical endpoint:

- `/estimate`

This receives extracted or manually edited form values, then calls the estimator service to calculate costing.

### `app/api/routes/batch/routes.py`

Batch upload and asynchronous-style processing endpoints.

Typical responsibilities:

- Start batch processing.
- Track file processing status.
- Retry a failed file.
- Return batch result JSON.
- Prepare batch Excel export data.

Important idea:

Batch processing allows many files to be queued and processed one by one or with limited concurrency, instead of blocking the frontend on one large request.

## 4. Core Configuration

### `app/core/config.py`

Central place for environment variables and settings.

Important settings include:

- Gemini API key or Vertex AI mode.
- Google Cloud project.
- Google Cloud location.
- Gemini model name.
- Default costing rates.
- Stock sizes such as rod length and sheet size.

The backend reads `.env` values from the project root.

## 5. Data Models

### `app/models/schemas.py`

Contains Pydantic models used for request and response validation.

This file defines the structure for:

- Extracted dimensions.
- Structured part breakdown.
- Costing response.
- Line items.
- Calculation steps.
- Material summary.
- Stock summary.
- Batch processing result.

Why this matters:

Gemini can return unpredictable JSON. Pydantic schemas make sure the backend response is consistent before sending it to the frontend.

## 6. Services

### `app/services/vision.py`

Handles AI-based extraction and image preprocessing.

Main responsibilities:

- Convert TIFF to PNG before sending to Gemini or showing in browser.
- Call Gemini API or Vertex AI model.
- Ask Gemini to extract dimensions and material/costing parameters.
- Parse and repair Gemini JSON where possible.
- Return validated extraction data.

Gemini is used for:

- OCR-style reading of title block and BOM.
- Finding part names, material codes, dimensions, quantities, and child references.
- Extracting structured cost breakdown information from drawings.

### `app/services/formulas.py`

Contains engineering and costing formula helpers.

Examples:

- Square tube weight.
- Round tube weight.
- Rod/bar weight.
- Plate weight.
- Surface area.
- Cutting length.
- Scrap/offcut calculation.
- Stock nesting/yield formulas.

Keep formulas here when possible so calculation logic is not mixed directly into routes.

### `app/services/estimator.py`

Main cost calculation engine.

It uses extracted/user-edited values and returns:

- Part-wise weight.
- Gross raw material weight.
- Scrap weight.
- Scrap value.
- Material cost.
- Laser cutting cost.
- Press/punching cost.
- Bending cost.
- Welding cost.
- Painting/surface cost.
- Total part cost.
- Total assembly/project cost.
- Simple calculation breakdowns.

## 7. Important Costing Concepts

### Material Rate

Default values:

- Mild Steel: INR 60/kg
- Stainless Steel: INR 240/kg
- Aluminium: INR 200/kg
- Copper: configurable market rate

User can override rates from the frontend.

### Stock Sizes

Default stock sizes:

- Rod/profile stock length: 6000 mm
- Sheet/plate stock size: 2500 x 1250 mm

### Scrap / Offcut

Scrap is calculated from leftover stock material after parts are cut.

Scrap value is:

```text
Scrap value = Scrap weight x Scrap rate per kg
```

Net material cost should consider:

```text
Net material cost = Gross raw material cost - Scrap resale value
```

### Nesting

Nesting means estimating how many parts can fit in one raw stock sheet or rod.

For rods:

```text
Parts per rod = floor(6000 / part length)
```

For sheets:

```text
Parts per sheet = floor(sheet length / part length) x floor(sheet width / part width)
```

The backend uses a simple nesting estimate. Real CNC nesting can improve yield.

## 8. Environment Variables

Common variables:

```text
GEMINI_PROVIDER=gemini_api or vertex_ai
GEMINI_API_KEY=your_api_key_for_standalone_gemini
GEMINI_MODEL=gemini-3.5-flash or configured Vertex model
GOOGLE_CLOUD_PROJECT=your_gcp_project
GOOGLE_CLOUD_LOCATION=asia-south1
GOOGLE_APPLICATION_CREDENTIALS=path_or_render_secret_file_for_vertex_service_account
```

For local Gemini API key mode, `GEMINI_API_KEY` is enough.

For Vertex AI mode, local ADC or service account credentials are required.

On Render, local ADC does not work. Use a service account JSON secret.

## 9. Main API Endpoints

### Health and Config

```text
GET /health
GET /gemini-config
GET /vertex-config
```

### Single File

```text
POST /diagram-preview
POST /extract-dimensions
POST /extract-structured
POST /extract-cost-breakdown
POST /estimate
```

### Batch

```text
POST /batch/start
GET /batch/status/{job_id}
POST /batch/retry
```

Endpoint names may differ slightly based on routing, but this is the intended flow.

## 10. Developer Notes

When adding new backend work:

- Add request/response shapes in `app/models/schemas.py`.
- Add calculation logic in `app/services/formulas.py` or `app/services/estimator.py`.
- Add Gemini prompt/extraction logic in `app/services/vision.py`.
- Add API endpoint in the correct route folder under `app/api/routes`.
- Keep route files thin. Routes should call services, not contain heavy formulas.
- Run compile check:

```bash
python -m compileall app
```

## 11. Deployment Notes

Backend can be deployed on Render, Cloud Run, or any Python web service platform.

Start command example:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

For Render:

- Set all required environment variables.
- If using Vertex AI, add service account credentials.
- Make sure billing and Vertex AI API are enabled in the Google Cloud project.

For Cloud Run:

- Use a service account with Vertex AI permissions.
- Set environment variables in Cloud Run.
- Cloud Run is a good option when frontend and backend should stay on Google Cloud.
