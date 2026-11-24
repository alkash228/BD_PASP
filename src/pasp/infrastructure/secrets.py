from __future__ import annotations

import keyring

_SERVICE = "BD_PASP"


def save_password(profile_id: str, password: str) -> None:
    keyring.set_password(_SERVICE, profile_id, password)


def load_password(profile_id: str) -> str:
    try:
        return keyring.get_password(_SERVICE, profile_id) or ""
    except Exception:
        return ""


def delete_password(profile_id: str) -> None:
    try:
        keyring.delete_password(_SERVICE, profile_id)
    except Exception:
        return
