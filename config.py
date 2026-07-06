"""Single source of truth for configuration.

The original repo loaded `config.json` independently in three separate
files (`__init__.py`, `cad_controller.py`, `nlp_processor.py`), each with
its own relative-path resolution and, in one case, a second hardcoded
fallback config that could silently drift from the real file. This module
replaces all of that with one settings model: an optional JSON file merged
with `CADMCP_*` environment variable overrides.

Precedence: values explicitly present in the JSON config file win over
environment variables, which win over the defaults below. This lets a
deployment pin specific values in the file while still using env vars for
anything the file leaves unset (e.g. secrets, per-environment output paths).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseModel):
    name: str = "CAD MCP Server"
    version: str = "2.0.0"


class CADSettings(BaseModel):
    backend: str = "dxf"
    startup_wait_time: float = 20.0

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        from cad.registry import available_backends

        if value.lower() not in available_backends():
            raise ValueError(f"cad.backend must be one of {available_backends()}, got {value!r}")
        return value.lower()


class OutputSettings(BaseModel):
    directory: str = "./output"
    default_filename: str = "cad_drawing.dxf"


class StorageSettings(BaseModel):
    directory: str = "./projects"


class PluginSettings(BaseModel):
    # Not created by default: an absent directory just means no plugins
    # are loaded, not an error (see plugins/loader.py).
    directory: str = "./plugins_installed"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CADMCP_", env_nested_delimiter="__")

    server: ServerSettings = Field(default_factory=ServerSettings)
    cad: CADSettings = Field(default_factory=CADSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    plugins: PluginSettings = Field(default_factory=PluginSettings)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Settings":
        file_data = {}
        path = Path(config_path or os.environ.get("CADMCP_CONFIG_FILE", "config.json"))
        if path.is_file():
            file_data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**file_data)
