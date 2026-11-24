from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from pasp.paths import profiles_path


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    server: str
    database: str
    auth: str
    username: str
    trust_cert: bool = True

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def load_profiles() -> list[Profile]:
    path = profiles_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    profiles: list[Profile] = []
    for item in payload.get("profiles", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        auth = item.get("auth")
        profiles.append(
            Profile(
                id=str(item["id"]),
                name=str(item.get("name") or item.get("server") or "Профиль"),
                server=str(item.get("server") or ""),
                database=str(item.get("database") or ""),
                auth=auth if auth in {"windows", "sql"} else "windows",
                username=str(item.get("username") or ""),
                trust_cert=bool(item.get("trust_cert", True)),
            )
        )
    return profiles


def save_profiles(profiles: list[Profile]) -> None:
    path = profiles_path()
    temporary = path.with_suffix(".json.tmp")
    payload = {"profiles": [asdict(profile) for profile in profiles]}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def upsert_profile(profile: Profile) -> None:
    others = [item for item in load_profiles() if item.id != profile.id]
    save_profiles([profile, *others])


def delete_profile(profile_id: str) -> None:
    save_profiles([item for item in load_profiles() if item.id != profile_id])
