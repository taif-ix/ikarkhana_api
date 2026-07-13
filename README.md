# Diagram Cost Estimator POC

Small proof of concept with a frontend and FastAPI backend.

The user uploads a drawing file, extracts dimensions through the Gemini API, reviews editable dimensions and rates, then gets a costing breakdown for a pillar assembly style part.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8010
```

Then open the frontend through FastAPI:

```text
http://127.0.0.1:8010/
```

## API

- `GET /health`
- `GET /gemini-config`
- `GET /vertex-config` compatibility alias
- `POST /diagram-preview` multipart form with `diagram`
- `POST /extract-dimensions` multipart form with `diagram`
- `POST /estimate` multipart form with `diagram` plus costing parameters

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
