"""Load credentials explicitly from the environment and a selected .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass
class SpotifyConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    cache_path: str = ".spotify_token_cache"


@dataclass
class YTMusicConfig:
    auth_file: str = "oauth.json"
    client_id: str = ""
    client_secret: str = ""


def _values(env_file: str | Path | None) -> tuple[dict[str, str | None], Path]:
    path = Path(env_file).expanduser().resolve() if env_file else Path.cwd() / ".env"
    if env_file and not path.is_file():
        raise FileNotFoundError(f"Environment file not found: {path}")
    return {**dotenv_values(path), **os.environ}, path.parent


def _path(value: str, root: Path) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else root / path)


def load_spotify_config(env_file: str | Path | None = None) -> SpotifyConfig:
    values, root = _values(env_file)
    client_id = values.get("SPOTIFY_CLIENT_ID")
    client_secret = values.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the environment or .env file")
    return SpotifyConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=values.get("SPOTIFY_REDIRECT_URI") or "http://127.0.0.1:8888/callback",
        cache_path=_path(values.get("SPOTIFY_CACHE_PATH") or ".spotify_token_cache", root),
    )


def load_ytmusic_config(env_file: str | Path | None = None) -> YTMusicConfig:
    values, root = _values(env_file)
    client_id = values.get("YTMUSIC_CLIENT_ID")
    client_secret = values.get("YTMUSIC_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("Set YTMUSIC_CLIENT_ID and YTMUSIC_CLIENT_SECRET for YouTube OAuth")
    auth_file = _path(values.get("YTMUSIC_AUTH_FILE") or "oauth.json", root)
    if not Path(auth_file).is_file():
        raise FileNotFoundError(f"YouTube OAuth file not found: {auth_file}. Run `ytmusicapi oauth` first")
    return YTMusicConfig(auth_file, client_id, client_secret)
