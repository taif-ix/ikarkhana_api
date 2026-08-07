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

