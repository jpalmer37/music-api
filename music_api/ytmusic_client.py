"""YouTube Music client wrapping ytmusicapi."""

from typing import Any, Dict, List, Optional

from ytmusicapi import YTMusic

from .config import YTMusicConfig, load_ytmusic_config


class YTMusicClient:
    """High-level wrapper around the YouTube Music API (via ytmusicapi).

    Provides access to playlists, library songs, and playlist management.

    Usage::

        from music_api import YTMusicClient

        yt = YTMusicClient()
        for pl in yt.list_playlists():
            print(pl["title"])
    """

    def __init__(self, config: Optional[YTMusicConfig] = None):
        """Initialize the client.

        Args:
            config: Auth config. If None, loads from environment variables.
                    Run `ytmusicapi oauth` first to create the auth file.
        """
        cfg = config or load_ytmusic_config()
        self.yt = YTMusic(cfg.auth_file)

    # ------------------------------------------------------------------
    # Playlists — listing & reading
    # ------------------------------------------------------------------

    def list_playlists(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Return the user's library playlists.

        Args:
            limit: Maximum playlists to fetch. Use a large number for all.
        """
        return self.yt.get_library_playlists(limit=limit)

    def get_playlist_tracks(
        self, playlist_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return all tracks in a playlist.

        Args:
            playlist_id: YouTube Music playlist ID.
            limit: Max tracks. None defaults to 5000.
        """
        playlist = self.yt.get_playlist(playlist_id, limit=limit or 5000)
        return playlist.get("tracks", [])

    def get_playlist(self, playlist_id: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """Return full playlist metadata and tracks.

        Args:
            playlist_id: YouTube Music playlist ID.
            limit: Max tracks. None defaults to 5000.
        """
        return self.yt.get_playlist(playlist_id, limit=limit or 5000)

    # ------------------------------------------------------------------
    # Playlists — management
    # ------------------------------------------------------------------

    def create_playlist(
        self,
        title: str,
        description: str = "",
        privacy: str = "PRIVATE",
        video_ids: Optional[List[str]] = None,
    ) -> str:
        """Create a new playlist.

        Args:
            title: Playlist name.
            description: Playlist description.
            privacy: One of 'PUBLIC', 'PRIVATE', 'UNLISTED'.
            video_ids: Optional list of video IDs to add on creation.

        Returns:
            The new playlist's ID.
        """
        return self.yt.create_playlist(
            title,
            description,
            privacy_status=privacy,
            video_ids=video_ids,
        )

    def delete_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """Delete a playlist.

        Args:
            playlist_id: ID of the playlist to delete.
        """
        return self.yt.delete_playlist(playlist_id)

    def add_tracks(self, playlist_id: str, video_ids: List[str]) -> Dict[str, Any]:
        """Add tracks to an existing playlist.

        Args:
            playlist_id: Target playlist ID.
            video_ids: List of video/song IDs to add.
        """
        return self.yt.add_playlist_items(playlist_id, video_ids)

    def remove_tracks(
        self, playlist_id: str, tracks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Remove tracks from a playlist.

        Args:
            playlist_id: Target playlist ID.
            tracks: Track dicts as returned by get_playlist_tracks().
                    These must contain the 'videoId' and 'setVideoId' fields.
        """
        return self.yt.remove_playlist_items(playlist_id, tracks)

    # ------------------------------------------------------------------
    # Library — liked & saved songs
    # ------------------------------------------------------------------

    def get_liked_songs(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Return the user's liked songs.

        Args:
            limit: Maximum songs to fetch.
        """
        playlist = self.yt.get_liked_songs(limit=limit)
        return playlist.get("tracks", [])

    def get_library_songs(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Return songs saved in the user's library.

        Args:
            limit: Maximum songs to fetch.
        """
        return self.yt.get_library_songs(limit=limit)

    def get_library_albums(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Return albums saved in the user's library.

        Args:
            limit: Maximum albums to fetch.
        """
        return self.yt.get_library_albums(limit=limit)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        filter: Optional[str] = "songs",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search the YouTube Music catalog.

        Args:
            query: Search query string.
            filter: One of 'songs', 'videos', 'albums', 'artists',
                    'playlists', or None for mixed results.
            limit: Number of results to return.
        """
        return self.yt.search(query, filter=filter, limit=limit)
