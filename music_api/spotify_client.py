"""Spotify client wrapping spotipy with transparent pagination."""

from typing import Any, Dict, Iterator, List, Optional

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .config import SpotifyConfig, load_spotify_config
from .retry import retry

SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"


class SpotifyClient:
    """High-level wrapper around the Spotify Web API.

    Handles authentication and provides paginated access to playlists,
    saved tracks, and saved albums.

    Usage::

        from music_api import SpotifyClient

        sp = SpotifyClient()
        for pl in sp.list_playlists():
            print(pl["name"])
    """

    def __init__(self, config: Optional[SpotifyConfig] = None):
        """Initialize the client.

        Args:
            config: Spotify credentials. If None, loads from environment variables.
        """
        cfg = config or load_spotify_config()
        auth_manager = SpotifyOAuth(
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            redirect_uri=cfg.redirect_uri,
            scope=SCOPES,
            cache_path=cfg.cache_path,
        )
        self.sp = spotipy.Spotify(auth_manager=auth_manager, retries=3)

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def list_playlists(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return the current user's playlists.

        Args:
            limit: Maximum number of playlists to return. None = all.
        """
        page_size = min(limit, 50) if limit else 50
        first_page = self._call(self.sp.current_user_playlists, limit=page_size)
        playlists = self._paginate_all(first_page, max_items=limit)
        return playlists

    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """Return full details for a single playlist (metadata + first page of tracks)."""
        return self._call(self.sp.playlist, playlist_id)

    def get_playlist_tracks(
        self, playlist_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return all tracks in a playlist.

        Args:
            playlist_id: Spotify playlist ID or URI.
            limit: Maximum tracks to return. None = all.
        """
        page_size = min(limit, 100) if limit else 100
        first_page = self._call(self.sp.playlist_tracks, playlist_id, limit=page_size)
        return self._paginate_all(first_page, max_items=limit)

    # ------------------------------------------------------------------
    # Saved / Liked Songs
    # ------------------------------------------------------------------

    def get_saved_tracks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return the user's liked / saved tracks.

        Args:
            limit: Maximum tracks to return. None = all.
        """
        page_size = min(limit, 50) if limit else 50
        first_page = self._call(self.sp.current_user_saved_tracks, limit=page_size)
        return self._paginate_all(first_page, max_items=limit)

    def iter_saved_tracks(self, page_size: int = 50) -> Iterator[Dict[str, Any]]:
        """Yield liked songs one at a time (memory-efficient for large libraries)."""
        page = self._call(self.sp.current_user_saved_tracks, limit=page_size)
        while True:
            for item in page["items"]:
                yield item
            if not page["next"]:
                break
            page = self._call(self.sp.next, page)

    # ------------------------------------------------------------------
    # Saved Albums
    # ------------------------------------------------------------------

    def get_saved_albums(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return the user's saved albums.

        Args:
            limit: Maximum albums to return. None = all.
        """
        page_size = min(limit, 50) if limit else 50
        first_page = self._call(self.sp.current_user_saved_albums, limit=page_size)
        return self._paginate_all(first_page, max_items=limit)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        search_type: str = "track",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Search the Spotify catalog.

        Args:
            query: Search query string.
            search_type: One of 'track', 'album', 'artist', 'playlist'.
            limit: Number of results (max 50).
        """
        return self._call(self.sp.search, q=query, type=search_type, limit=limit)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @retry(max_attempts=3, backoff_base=1.0, retryable_exceptions=(SpotifyException,))
    def _call(self, method, *args, **kwargs):
        """Call a spotipy method with automatic retry on rate-limit errors."""
        return method(*args, **kwargs)

    def _paginate_all(
        self, first_page: Dict[str, Any], max_items: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Exhaust a Spotify paging object, collecting all items."""
        items = list(first_page["items"])
        page = first_page

        while page["next"]:
            if max_items and len(items) >= max_items:
                break
            page = self._call(self.sp.next, page)
            items.extend(page["items"])

        if max_items:
            items = items[:max_items]

        return items
