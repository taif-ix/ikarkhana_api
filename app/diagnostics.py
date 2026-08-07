import inspect
import json
import os
from datetime import datetime, timezone


def cloud_print(
    event: str,
    *,
    severity: str = "INFO",
    message: str | None = None,
    **fields,
) -> None:
    current_frame = inspect.currentframe()
    caller_frame = current_frame.f_back if current_frame else None

    module_name = (
        caller_frame.f_globals.get("__name__", "unknown")
        if caller_frame
        else "unknown"
    )
    function_name = caller_frame.f_code.co_name if caller_frame else "unknown"
    service_name = os.getenv("K_SERVICE", "drawing-extractor-api")
    environment = os.getenv("APP_ENV", "dev")

    payload = {
        "severity": severity,
        "message": message or event,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "service": service_name,
        "environment": environment,
        "module": module_name,
        "function": function_name,
        **fields,
    }

    trace_id = fields.get("trace_id")
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if trace_id and project_id:
        payload["logging.googleapis.com/trace"] = (
            f"projects/{project_id}/traces/{trace_id}"
        )

    print(
        json.dumps(payload, ensure_ascii=False, default=str),
        flush=True,
    )
