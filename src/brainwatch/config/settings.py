from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class ProjectSettings:
    project_name: str
    architecture: str
    raw: dict[str, Any]


def load_settings(path: str | Path) -> ProjectSettings:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return ProjectSettings(
        project_name=payload["project_name"],
        architecture=payload["architecture"],
        raw=payload,
    )
