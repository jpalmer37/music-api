"""Spotify reads and playlist management through Spotipy."""

from typing import Any, Iterator

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import SpotifyConfig, load_spotify_config
from .identifiers import identifier
from .retry import read_call

SCOPES = "user-library-read playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"


class SpotifyClient:
    service = "spotify"

    def __init__(self, config: SpotifyConfig | None = None):
        cfg = config or load_spotify_config()
        auth = SpotifyOAuth(client_id=cfg.client_id, client_secret=cfg.client_secret,
                            redirect_uri=cfg.redirect_uri, scope=SCOPES, cache_path=cfg.cache_path)
        self.sp = spotipy.Spotify(auth_manager=auth, retries=0, status_retries=0, requests_timeout=30)

    def _pages(self, method, limit: int | None = None, **kwargs) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("Limit must be nonnegative")
        if limit == 0:
            return []
        page = read_call(method, limit=min(limit, 50) if limit else 50, **kwargs)
        items = list(page["items"])
        while page["next"] and (limit is None or len(items) < limit):
            page = read_call(self.sp.next, page)
            items.extend(page["items"])
        return items if limit is None else items[:limit]

    def list_playlists(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._pages(self.sp.current_user_playlists, limit)

    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        return read_call(self.sp.playlist, identifier(playlist_id, self.service))

    def get_playlist_tracks(self, playlist_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self._pages(self.sp.playlist_items, limit, playlist_id=identifier(playlist_id, self.service))

    def get_saved_tracks(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._pages(self.sp.current_user_saved_tracks, limit)

    def iter_saved_tracks(self, page_size: int = 50) -> Iterator[dict[str, Any]]:
        if not 1 <= page_size <= 50:
            raise ValueError("Page size must be between 1 and 50")
        page = read_call(self.sp.current_user_saved_tracks, limit=page_size)
        while True:
            yield from page["items"]
            if not page["next"]:
                break
            page = read_call(self.sp.next, page)

    def get_saved_albums(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._pages(self.sp.current_user_saved_albums, limit)

    def search(self, query: str, search_type: str = "track", limit: int = 10) -> dict[str, Any]:
        if not 1 <= limit <= 10:
            raise ValueError("Spotify search limit must be between 1 and 10")
        return read_call(self.sp.search, q=query, type=search_type, limit=limit)

    def get_track(self, track_id: str) -> dict[str, Any]:
        return read_call(self.sp.track, identifier(track_id, self.service, "track"))

    def create_playlist(self, title: str, description: str = "") -> str:
        result = self.sp.current_user_playlist_create(title, public=False, description=description)
        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("Spotify did not return a new playlist ID")
        return identifier(result["id"], self.service)

    def rename_playlist(self, playlist_id: str, title: str):
        return self.sp.playlist_change_details(identifier(playlist_id, self.service), name=title)

    def add_tracks(self, playlist_id: str, track_ids: list[str]):
        if not 1 <= len(track_ids) <= 100:
            raise ValueError("Add between 1 and 100 tracks per call")
        playlist_id = identifier(playlist_id, self.service)
        uris = [f"spotify:track:{identifier(t, self.service, 'track')}" for t in track_ids]
        # Spotipy 2.26.0 serializes a bare array; Spotify requires an object containing uris.
        result = self.sp._post(f"playlists/{playlist_id}/items", payload={"uris": uris})
        if not isinstance(result, dict) or not result.get("snapshot_id"):
            raise RuntimeError("Spotify did not confirm the addition with a snapshot ID")
        return result

    def remove_tracks(self, playlist_id: str, tracks: list[dict[str, Any]], snapshot_id: str | None = None):
        """Remove specified occurrences using URI and zero-based positions."""
        if not 1 <= len(tracks) <= 100 or any(not t.get("positions") for t in tracks):
            raise ValueError("Supply 1–100 tracks, each with a URI and positions")
        return self.sp.playlist_remove_specific_occurrences_of_items(
            identifier(playlist_id, self.service), tracks, snapshot_id=snapshot_id)

    def move_track(self, playlist_id: str, position: int, before: int, snapshot_id: str | None = None):
        """Move one occurrence before an index in the current playlist, or its length for the end."""
        if position < 0 or before < 0:
            raise ValueError("Positions must be nonnegative")
        return self.sp.playlist_reorder_items(identifier(playlist_id, self.service), position, before,
                                             snapshot_id=snapshot_id)
