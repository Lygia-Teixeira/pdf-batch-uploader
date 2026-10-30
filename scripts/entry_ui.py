"""Ponto de entrada do executável PyInstaller (abre a UI)."""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "playwright-browsers"
    if bundled.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))

from pdf_batch_uploader.ui.app import main  # noqa: E402

raise SystemExit(main())
