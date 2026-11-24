import os
from pathlib import Path

_DIR_NAME = "BD_PASP"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / _DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def queries_dir() -> Path:
    path = app_data_dir() / "queries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_path() -> Path:
    return app_data_dir() / "profiles.json"


def schedules_path() -> Path:
    return app_data_dir() / "schedules.json"
