"""Thin HTTP client used by every CLI command.

Reads token from credentials file at platformdirs.user_config_path/credentials.json.
Multi-profile via `--profile NAME` (default: `default`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from platformdirs import PlatformDirs

_DIRS = PlatformDirs("scryer", "scryer")
DEFAULT_PROFILE = "default"
DEFAULT_BASE_URL = "https://scryer-production.up.railway.app"


def credentials_path() -> Path:
    return Path(_DIRS.user_config_path) / "credentials.json"


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    token: str | None = None


def load_profile(name: str = DEFAULT_PROFILE) -> Profile:
    """Load a profile by name. Falls back to env vars / defaults."""
    base_url = os.environ.get("SCRYER_API_URL", DEFAULT_BASE_URL)
    env_token = os.environ.get("SCRYER_API_TOKEN")
    path = credentials_path()
    if path.exists():
        data = json.loads(path.read_text())
        if name in data:
            entry = data[name]
            return Profile(
                name=name,
                base_url=entry.get("base_url", base_url),
                token=entry.get("token") or env_token,
            )
    return Profile(name=name, base_url=base_url, token=env_token)


def save_profile(profile: Profile) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, dict[str, str]] = {}
    if path.exists():
        data = json.loads(path.read_text())
    entry: dict[str, str] = {"base_url": profile.base_url}
    if profile.token:
        entry["token"] = profile.token
    data[profile.name] = entry
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def client_for(profile: Profile) -> httpx.Client:
    headers = {}
    if profile.token:
        headers["Authorization"] = f"Bearer {profile.token}"
    return httpx.Client(base_url=profile.base_url, headers=headers, timeout=30.0)
