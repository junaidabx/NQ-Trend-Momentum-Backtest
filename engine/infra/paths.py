"""Resolve external files (.env, config.yaml, logs/) relative to the app. When
frozen by PyInstaller/flet pack these sit next to the executable; in a source
checkout they resolve to the project root. Secrets and editable config stay out
of the bundle so the API key is never baked into the binary.
"""
from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    """Directory that holds the app's external files."""
    if getattr(sys, "frozen", False):  # PyInstaller / flet pack
        return Path(sys.executable).resolve().parent
    # engine/infra/paths.py → repo root is two levels above infra/
    return Path(__file__).resolve().parents[2]


def app_path(name: str) -> Path:
    """Absolute path to an external file/dir next to the app."""
    return base_dir() / name
