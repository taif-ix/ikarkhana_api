from __future__ import annotations

import os
from pathlib import Path


SS304_DENSITY_KG_PER_MM3 = 7.9e-6
CURRENCY_UNIT = "INR"
RATE_MS_PER_KG = 60.00
RATE_SS_PER_KG = 240.00
RATE_ALUMINIUM_PER_KG = 200.00
RATE_COPPER_PER_KG = 900.00
RATE_PER_KG = RATE_SS_PER_KG
RATE_PER_CUT_METER = 200.00
RATE_PER_BEND_STROKE = 2.00
RATE_PER_SQ_METER_PAINT = 120.00
RATE_PER_PRESS_MACHINE_HIT = 5.00
LABOR_WELDING_PER_METER = 22.00
LABOR_TACKING_FIXED = 1040.00
SCRAP_RATE_PER_KG = 28.00
ROD_STOCK_LENGTH_MM = 6000.00
SHEET_STOCK_LENGTH_MM = 2500.00
SHEET_STOCK_WIDTH_MM = 1250.00
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_GEMINI_MODELS = {
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
}

MATERIALS = {
    "ms": {
        "label": "Mild Steel",
        "density": 7.85e-6,
        "default_rate": RATE_MS_PER_KG,
        "codes": ["ms", "mild steel", "is2062", "e250", "e350"],
    },
    "ss": {
        "label": "Stainless Steel",
        "density": SS304_DENSITY_KG_PER_MM3,
        "default_rate": RATE_SS_PER_KG,
        "codes": ["ss", "stainless", "304", "316", "c-k201", "k201", "ck201"],
    },
    "aluminium": {
        "label": "Aluminium",
        "density": 2.70e-6,
        "default_rate": RATE_ALUMINIUM_PER_KG,
        "codes": ["al", "alu", "aluminium", "aluminum", "6061", "6082"],
    },
    "copper": {
        "label": "Copper",
        "density": 8.96e-6,
        "default_rate": RATE_COPPER_PER_KG,
        "codes": ["cu", "copper", "c11000", "etp"],
    },
}


def load_project_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("$env:"):
            line = line.removeprefix("$env:")

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
