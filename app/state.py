from typing import Any, Dict


# Shared Gemini client initialized when FastAPI starts.
client = None

# Stores uploaded file batches and generated outputs by session id.
session_cache: Dict[str, Dict[str, Any]] = {}

# Avoids repeating Gemini extraction for the same uploaded drawing bytes.
GLOBAL_BLUEPRINT_CACHE: Dict[str, Dict[str, Any]] = {}
