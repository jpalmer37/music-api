"""YouTube Music reads and playlist management through ytmusicapi."""

from typing import Any

from ytmusicapi import OAuthCredentials, YTMusic

from .config import YTMusicConfig, load_ytmusic_config
from .identifiers import identifier
from .retry import read_call


def _write_result(result):
    status = result if isinstance(result, str) else result.get("status")
    if status != "STATUS_SUCCEEDED":
        raise RuntimeError(f"YouTube Music did not confirm the write: {status or 'missing status'}")
    return result


class YTMusicClient:
    service = "ytmusic"

    def __init__(self, config: YTMusicConfig | None = None):
        cfg = config or load_ytmusic_config()
        self.yt = YTMusic(cfg.auth_file, oauth_credentials=OAuthCredentials(cfg.client_id, cfg.client_secret))

    def list_playlists(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._list(self.yt.get_library_playlists, limit)

    def _list(self, method, limit: int | None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("Limit must be nonnegative")
        if limit == 0:
            return []
        items = read_call(method, limit=limit)
        return items if limit is None else items[:limit]

    def get_playlist(self, playlist_id: str, limit: int | None = None) -> dict[str, Any]:
        if limit is not None and limit < 0:
            raise ValueError("Limit must be nonnegative")
        playlist = read_call(self.yt.get_playlist, identifier(playlist_id, self.service), limit=limit)
        if limit is not None:
            playlist["tracks"] = playlist["tracks"][:limit]
        return playlist

    def get_playlist_tracks(self, playlist_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        if limit == 0:
            return []
        return self.get_playlist(playlist_id, limit)["tracks"]

    def get_track(self, track_id: str) -> dict[str, Any]:
        result = read_call(self.yt.get_song, identifier(track_id, self.service, "track"))
        details = result.get("videoDetails")
        if not details or result.get("playabilityStatus", {}).get("status") != "OK":
            raise ValueError("YouTube track is unavailable")
        return {"videoId": details["videoId"], "title": details["title"],
                "artists": [{"name": details.get("author", "")}],
                "duration_seconds": int(details["lengthSeconds"]), "isAvailable": True}

    def create_playlist(self, title: str, description: str = "", privacy: str = "PRIVATE",
                        video_ids: list[str] | None = None) -> str:
        result = self.yt.create_playlist(title, description, privacy_status=privacy, video_ids=video_ids)
        if not isinstance(result, str):
            raise RuntimeError("YouTube Music did not return a new playlist ID")
        return identifier(result, self.service)

    def rename_playlist(self, playlist_id: str, title: str):
        return _write_result(self.yt.edit_playlist(identifier(playlist_id, self.service), title=title))

    def delete_playlist(self, playlist_id: str):
        return _write_result(self.yt.delete_playlist(identifier(playlist_id, self.service)))

    def add_tracks(self, playlist_id: str, video_ids: list[str], duplicates: bool = False):
        if not 1 <= len(video_ids) <= 100:
            raise ValueError("Add between 1 and 100 tracks per call")
        return _write_result(self.yt.add_playlist_items(identifier(playlist_id, self.service),
            [identifier(t, self.service, "track") for t in video_ids], duplicates=duplicates))

    def remove_tracks(self, playlist_id: str, tracks: list[dict[str, Any]]):
        if not tracks or any(not t.get("videoId") or not t.get("setVideoId") for t in tracks):
            raise ValueError("Each occurrence must contain videoId and setVideoId")
        return _write_result(self.yt.remove_playlist_items(identifier(playlist_id, self.service), tracks))

    def move_track(self, playlist_id: str, position: int, before: int):
        """Move an occurrence before a current index; use playlist length for the end."""
        tracks = self.get_playlist_tracks(playlist_id)
        if not 0 <= position < len(tracks) or not 0 <= before <= len(tracks):
            raise ValueError("Position outside the playlist")
        if before in {position, position + 1}:
            return None
        move = tracks[position]["setVideoId"]
        if before < len(tracks):
            move = (move, tracks[before]["setVideoId"])
        return _write_result(self.yt.edit_playlist(identifier(playlist_id, self.service), moveItem=move))

    def get_liked_songs(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.get_playlist("LM", limit)["tracks"]

    def get_library_songs(self, limit: int = 5000) -> list[dict[str, Any]]:
        return self._list(self.yt.get_library_songs, limit)

    def get_library_albums(self, limit: int = 5000) -> list[dict[str, Any]]:
        return self._list(self.yt.get_library_albums, limit)

    def search(self, query: str, filter: str | None = "songs", limit: int = 10) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Search limit must be positive")
        return read_call(self.yt.search, query, filter=filter, limit=limit)[:limit]
