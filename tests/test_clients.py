import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import spotipy
from spotipy.exceptions import SpotifyException

from music_api import config
from music_api.config import SpotifyConfig, YTMusicConfig, load_spotify_config, load_ytmusic_config
from music_api.identifiers import identifier
from music_api.retry import read_call
from music_api.spotify_client import SpotifyClient
from music_api.ytmusic_client import YTMusicClient


class ConfigTests(unittest.TestCase):
    def test_explicit_file_environment_override_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "override"}, clear=True):
            root = Path(temp).resolve()
            (root / "oauth.json").write_text("{}")
            env = root / ".env"
            env.write_text("SPOTIFY_CLIENT_ID=file\nSPOTIFY_CLIENT_SECRET=secret\nYTMUSIC_CLIENT_ID=google\nYTMUSIC_CLIENT_SECRET=google-secret\nYTMUSIC_AUTH_FILE=oauth.json\n")
            before = dict(os.environ)
            sp = load_spotify_config(env)
            yt = load_ytmusic_config(env)
            self.assertEqual(sp.client_id, "override")
            self.assertEqual(sp.redirect_uri, "http://127.0.0.1:8888/callback")
            self.assertEqual(sp.cache_path, str(root / ".spotify_token_cache"))
            self.assertEqual(yt.auth_file, str(root / "oauth.json"))
            self.assertEqual(yt.client_id, "google")
            self.assertEqual(dict(os.environ), before)

    def test_import_does_not_load_credentials(self):
        with patch("dotenv.dotenv_values") as read:
            importlib.reload(config)
            read.assert_not_called()

    def test_missing_credentials_and_explicit_file(self):
        with patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                load_spotify_config(Path(temp) / "missing")
            env = Path(temp) / ".env"
            env.write_text("")
            with self.assertRaises(ValueError):
                load_spotify_config(env)
            with self.assertRaises(ValueError):
                load_ytmusic_config(env)


class ClientTests(unittest.TestCase):
    def test_spotify_constructor_disables_sdk_retries(self):
        with patch("music_api.spotify_client.SpotifyOAuth") as auth, patch("music_api.spotify_client.spotipy.Spotify") as sdk:
            SpotifyClient(SpotifyConfig("id", "secret"))
            self.assertIn("playlist-modify-private", auth.call_args.kwargs["scope"])
            self.assertEqual(sdk.call_args.kwargs["retries"], 0)
            self.assertEqual(sdk.call_args.kwargs["status_retries"], 0)

    def test_spotify_add_transport_contract(self):
        response = requests.Response()
        response.status_code = 201
        response._content = b'{"snapshot_id":"new"}'
        session = requests.Session()
        session.request = Mock(return_value=response)
        client = SpotifyClient.__new__(SpotifyClient)
        client.sp = spotipy.Spotify(auth="offline-test-token", requests_session=session, retries=0, status_retries=0)
        self.assertEqual(client.add_tracks("mix", ["song1", "spotify:track:song1"]), {"snapshot_id": "new"})
        args, kwargs = session.request.call_args
        self.assertEqual(args, ("POST", "https://api.spotify.com/v1/playlists/mix/items"))
        self.assertEqual(json.loads(kwargs["data"]), {"uris": ["spotify:track:song1", "spotify:track:song1"]})
        self.assertEqual(session.request.call_count, 1)

    def test_pagination_limits_and_current_endpoint(self):
        client = SpotifyClient.__new__(SpotifyClient)
        client.sp = Mock()
        client.sp.playlist_items.return_value = {"items": list(range(50)), "next": "page2"}
        client.sp.next.return_value = {"items": list(range(50, 60)), "next": None}
        self.assertEqual(client.get_playlist_tracks("mix", 53), list(range(53)))
        self.assertEqual(client.sp.playlist_items.call_args.kwargs["limit"], 50)
        self.assertEqual(client.get_playlist_tracks("mix", 0), [])
        with self.assertRaises(ValueError):
            client.get_playlist_tracks("mix", -1)
        with self.assertRaises(ValueError):
            client.search("title", limit=20)

    def test_youtube_oauth_and_unlimited_playlist_reads(self):
        with patch("music_api.ytmusic_client.YTMusic") as sdk:
            client = YTMusicClient(YTMusicConfig("oauth.json", "client", "secret"))
            self.assertIsNotNone(sdk.call_args.kwargs["oauth_credentials"])
            client.get_playlist("mix")
            sdk.return_value.get_playlist.assert_called_once_with("mix", limit=None)

    def test_youtube_duplicate_and_failed_status_handling(self):
        client = YTMusicClient.__new__(YTMusicClient)
        client.yt = Mock()
        client.yt.add_playlist_items.return_value = {"status": "STATUS_SUCCEEDED"}
        client.add_tracks("mix", ["song", "song"], duplicates=True)
        client.yt.add_playlist_items.assert_called_once_with("mix", ["song", "song"], duplicates=True)
        client.yt.add_playlist_items.return_value = {"status": "STATUS_FAILED"}
        with self.assertRaises(RuntimeError):
            client.add_tracks("mix", ["song"])
        self.assertEqual(client.yt.add_playlist_items.call_count, 2)

    def test_youtube_limits_trim_sdk_overshoot_and_zero_does_not_read(self):
        client = YTMusicClient.__new__(YTMusicClient)
        client.yt = Mock()
        client.yt.get_library_playlists.return_value = list(range(25))
        self.assertEqual(client.list_playlists(2), [0, 1])
        client.yt.get_library_playlists.reset_mock()
        self.assertEqual(client.list_playlists(0), [])
        client.yt.get_library_playlists.assert_not_called()
        with self.assertRaises(ValueError):
            client.list_playlists(-1)

    def test_youtube_moving_uses_occurrence_ids(self):
        client = YTMusicClient.__new__(YTMusicClient)
        client.yt = Mock()
        client.yt.get_playlist.return_value = {"tracks": [{"videoId": "same", "setVideoId": "first"}, {"videoId": "same", "setVideoId": "second"}, {"videoId": "other", "setVideoId": "third"}]}
        client.yt.edit_playlist.return_value = "STATUS_SUCCEEDED"
        client.move_track("mix", 0, 2)
        client.yt.edit_playlist.assert_called_once_with("mix", moveItem=("first", "third"))
        client.yt.edit_playlist.reset_mock()
        client.move_track("mix", 0, 3)
        client.yt.edit_playlist.assert_called_once_with("mix", moveItem="first")


class RetryTests(unittest.TestCase):
    def test_retry_after_and_bounded_reads(self):
        rate = SpotifyException(429, -1, "rate", headers={"Retry-After": "7"})
        read = Mock(side_effect=[rate, {"ok": True}])
        with patch("music_api.retry.time.sleep") as sleep:
            self.assertEqual(read_call(read), {"ok": True})
            sleep.assert_called_once_with(7)
        read = Mock(side_effect=SpotifyException(503, -1, "temporary"))
        with patch("music_api.retry.time.sleep"):
            with self.assertRaises(SpotifyException):
                read_call(read)
        self.assertEqual(read.call_count, 3)

    def test_auth_quota_and_long_waits_are_terminal(self):
        for error in (SpotifyException(401, -1, "auth"), SpotifyException(429, -1, "quota", reason="QUOTA_EXCEEDED"),
                      SpotifyException(429, -1, "wait", headers={"Retry-After": "120"})):
            read = Mock(side_effect=error)
            with patch("music_api.retry.time.sleep") as sleep:
                with self.assertRaises(SpotifyException):
                    read_call(read)
                sleep.assert_not_called()
            self.assertEqual(read.call_count, 1)

    def test_read_timeout_is_retried(self):
        read = Mock(side_effect=[requests.exceptions.Timeout(), "ok"])
        with patch("music_api.retry.time.sleep"):
            self.assertEqual(read_call(read), "ok")


class IdentifierTests(unittest.TestCase):
    def test_service_links_and_uris(self):
        cases = [("spotify:playlist:abc", "spotify", "playlist", "abc"),
                 ("https://open.spotify.com/intl-ca/track/abc?si=xyz", "spotify", "track", "abc"),
                 ("https://music.youtube.com/playlist?list=PL_abc", "ytmusic", "playlist", "PL_abc"),
                 ("https://music.youtube.com/watch?v=abc&list=PL_mix", "ytmusic", "track", "abc"),
                 ("https://youtu.be/abc", "ytmusic", "track", "abc")]
        for value, service, kind, expected in cases:
            self.assertEqual(identifier(value, service, kind), expected)
        for value in ("https://evil.example/mix", "../secret", "spotify:track:abc", ""):
            with self.assertRaises(ValueError):
                identifier(value, "spotify")
