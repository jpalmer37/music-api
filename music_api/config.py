"""Load credentials from environment variables or a .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (sessions/music-api/.env)
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")


@dataclass
class SpotifyConfig:
    """Spotify API credentials."""

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8888/callback"
    cache_path: str = ".spotify_token_cache"


@dataclass
class YTMusicConfig:
    """YouTube Music authentication config."""

    auth_file: str = "oauth.json"


def load_spotify_config() -> SpotifyConfig:
    """Build SpotifyConfig from environment variables.

    Expected env vars: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
    and optionally SPOTIFY_REDIRECT_URI.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
    cache_path = os.environ.get("SPOTIFY_CACHE_PATH", ".spotify_token_cache")

    if not client_id or not client_secret:
        raise ValueError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set. "
            "Copy .env.example to .env and fill in your credentials."
        )

    return SpotifyConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        cache_path=cache_path,
    )


def load_ytmusic_config() -> YTMusicConfig:
    """Build YTMusicConfig from environment variables.

    Expected env var: YTMUSIC_AUTH_FILE (defaults to oauth.json).
    Run `ytmusicapi oauth` to generate the auth file.
    """
    auth_file = os.environ.get("YTMUSIC_AUTH_FILE", "oauth.json")

    if not Path(auth_file).exists():
        raise FileNotFoundError(
            f"YouTube Music auth file not found: {auth_file}. "
            "Run `ytmusicapi oauth` to generate it, or set YTMUSIC_AUTH_FILE "
            "to the correct path."
        )

    return YTMusicConfig(auth_file=auth_file)
