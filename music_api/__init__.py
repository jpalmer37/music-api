"""Music API Toolkit — Spotify and YouTube Music clients."""

from .config import (
    SpotifyConfig,
    YTMusicConfig,
    load_spotify_config,
    load_ytmusic_config,
)
from .spotify_client import SpotifyClient
from .ytmusic_client import YTMusicClient

__all__ = [
    "SpotifyClient",
    "YTMusicClient",
    "SpotifyConfig",
    "YTMusicConfig",
    "load_spotify_config",
    "load_ytmusic_config",
]
