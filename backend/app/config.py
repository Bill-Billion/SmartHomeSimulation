from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(
    os.getenv(
        "SMART_HOME_DATA_ROOT",
        REPO_ROOT / "backend" / "data" / "generated",
    )
)

DEFAULT_WALL_HEIGHT_M = 2.8
DEFAULT_WALL_THICKNESS_M = 0.2
DEFAULT_TARGET_SPAN_M = 12.0
SUPPORTED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".dxf", ".dwg"}
SUPPORTED_PARSE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".dxf"}

