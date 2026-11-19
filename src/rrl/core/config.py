"""Configuration: a workspace directory outside the repo (db, store, logs, rrl.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKSPACE = "~/RRL-Workspace"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    workspace: Path
    xemu: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.workspace / "rrl.db"

    @property
    def store_path(self) -> Path:
        return self.workspace / "store"

    @property
    def log_path(self) -> Path:
        return self.workspace / "logs" / "rrl.log"

    @property
    def config_file(self) -> Path:
        return self.workspace / "rrl.toml"


def load_config() -> Config:
    """Workspace from $RRL_WORKSPACE or ~/RRL-Workspace; settings from <workspace>/rrl.toml."""
    workspace = Path(os.environ.get("RRL_WORKSPACE", DEFAULT_WORKSPACE)).expanduser().resolve()
    data: dict[str, object] = {}
    config_file = workspace / "rrl.toml"
    if config_file.exists():
        try:
            data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_file}: {exc}") from exc

    emulators = data.get("emulators", {})
    if not isinstance(emulators, dict):
        raise ConfigError(f"{config_file}: [emulators] must be a table")
    xemu = emulators.get("xemu")
    if xemu is not None and not isinstance(xemu, str):
        raise ConfigError(f"{config_file}: emulators.xemu must be a path string")
    return Config(workspace=workspace, xemu=Path(xemu).expanduser() if xemu else None)


def ensure_workspace(config: Config) -> None:
    for d in (config.workspace, config.store_path, config.log_path.parent):
        d.mkdir(parents=True, exist_ok=True)
