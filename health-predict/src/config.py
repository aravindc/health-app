"""Loads config/settings.yaml and config/mappings.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@lru_cache
def settings() -> dict[str, Any]:
    return _load(CONFIG_DIR / "settings.yaml")


@lru_cache
def mappings() -> dict[str, Any]:
    return _load(CONFIG_DIR / "mappings.yaml")


@lru_cache
def models() -> dict[str, Any]:
    return _load(CONFIG_DIR / "models.yaml")


def _load(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def path(rel: str) -> Path:
    """Resolve a project-relative path from the config."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p
