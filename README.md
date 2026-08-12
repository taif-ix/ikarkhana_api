# Cost Estimator API

FastAPI backend for engineering diagram cost estimation.

The frontend is maintained separately:

```text
https://github.com/taif-ix/ikarkhana_web
```

This backend repo should stay API-only. It no longer serves bundled HTML/CSS/JS.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010
```

Check the backend:

```text
http://127.0.0.1:8010/health
```

Run the frontend from the separate FE repo and configure it to call:

```text
http://127.0.0.1:8010
```

## API

### Backend batch processing

The main backend starts an asynchronous extraction job with:

```http
POST /process-drawings
Content-Type: application/json
X-API-Key: <AI_API_KEY>
```

```json
{
  "job_id": "JOB-20260811-001",
  "callback_url": "https://api.ikarkhana.com/ai/callback",
  "files": [
    {
      "analysis_id": 101,
      "file_url": "https://storage.googleapis.com/bucket/drawing-001.png",
      "filename": "drawing-001.png"
    }
  ]
}
```

The AI API immediately responds with HTTP `202 Accepted`:

```json
{
  "job_id": "JOB-20260811-001",
  "status": "ACCEPTED",
  "file_count": 1
}
```

Each file is downloaded and processed independently. On completion, the AI API sends one `POST` to `callback_url` for each `analysis_id`:

```json
{
  "job_id": "JOB-20260811-001",
  "analysis_id": 101,
  "status": "COMPLETED",
  "analysis": {},
  "bom": []
}
```

If a file cannot be downloaded or extracted, its callback is:

```json
{
  "job_id": "JOB-20260811-001",
  "analysis_id": 101,
  "status": "FAILED",
  "error": "failure details"
}
```

The backend must return a successful `2xx` response to callbacks. Failed callback delivery is retried three times by default. Configure integration behavior with:

```text
AI_API_KEY=<secret accepted from the backend>
BACKEND_CALLBACK_API_KEY=<secret sent to the backend callback>
AI_BATCH_CONCURRENCY=3
CALLBACK_TIMEOUT_SECONDS=20
CALLBACK_MAX_ATTEMPTS=3
DEFAULT_MATERIAL_RATE_PER_KG=100
SECONDS_PER_OPERATION=5
```

`file_url` must be a direct public or signed read URL, not the Cloud Console object page. URLs copied into JSON must be raw URLs; Markdown syntax such as `[url](url)` is invalid.

- `GET /health`
- `GET /` API metadata
- `GET /gemini-config`
- `GET /vertex-config` compatibility alias
- `POST /diagram-preview` multipart form with `diagram`
- `POST /extract-dimensions` multipart form with `diagram`
- `POST /calculate-cost-breakdown` JSON endpoint that recalculates structured costing from extracted data

### Backend extraction from Cloud Storage

`POST /extractions/from-gcs` reads an uploaded drawing from Cloud Storage and returns
the extracted BOM and costing data as JSON.

```json
{
  "gcs_uri": "gs://ikarkhana-uploads/drawings/example.png"
}
```

Supported formats are JPEG, PNG, PDF, and TIFF, with a maximum object size of 25 MB.
The AI API runtime service account needs `roles/storage.objectViewer` on the upload
bucket. Keep the AI Cloud Run service private and grant the calling backend runtime
service account `roles/run.invoker` on it.

## Gemini API Extraction

Dimension extraction uses Gemini API. This avoids Vertex AI setup for the POC, but you still need a valid Gemini API key and available quota.

Install the packages:

```powershell
pip install -r requirements.txt
```

Configure `.env` or the current PowerShell session:

```powershell
$env:GEMINI_API_KEY="your-gemini-api-key"
$env:GEMINI_MODEL="gemini-3.5-flash"
```

Check config:

```text
http://127.0.0.1:8010/gemini-config
```

The extraction endpoint sends the uploaded drawing image to Gemini and asks for JSON dimensions. The costing endpoint still uses local Python formulas.

