import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from music_api.cli import main
from music_api.transfer import load_transfer_plan
from test_transfer import FIXTURE, FakeClient


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.source = FakeClient("spotify", [FIXTURE["spotify"]])
        self.destination = FakeClient("ytmusic")
        self.clients = patch("music_api.cli._client", side_effect=lambda service, env: self.source if service == "spotify" else self.destination)
        self.clients.start()
        self.addCleanup(self.clients.stop)
        state = patch("music_api.cli.STATE_DIR", self.root / "state")
        state.start()
        self.addCleanup(state.stop)

    def transfer(self, *extra):
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()), patch("sys.stdin.isatty", return_value=False):
            return main(["transfer", "--from", "spotify", "--to", "ytmusic", "--playlist", "source", "--run-dir", str(self.run), *extra])

    def test_dry_run_saves_plan_and_never_mutates_remote(self):
        self.assertEqual(self.transfer("--dry-run"), 0)
        self.assertTrue((self.run / "plan.json").is_file())
        self.assertEqual(self.destination.creations, 0)
        self.assertEqual(self.destination.additions, [])
        self.assertFalse((self.run / "progress.json").exists())

    def test_noninteractive_review_stops_before_writes(self):
        alternative = copy.deepcopy(FIXTURE["ytmusic"])
        alternative["videoId"] = "another"
        self.destination.catalog.append(alternative)
        self.assertEqual(self.transfer(), 2)
        self.assertEqual(self.destination.creations, 0)
        self.assertEqual(load_transfer_plan(self.run).playlists[0].entries[0].decision, "unresolved")

    def test_interactive_resume_saves_mapping_and_applies(self):
        self.destination.catalog[0]["title"] = "Winter Sun (Live)"
        self.assertEqual(self.transfer("--dry-run"), 0)
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="1"), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["resume", str(self.run)]), 0)
        self.assertTrue((self.root / "state" / "choices.json").is_file())
        self.assertEqual(load_transfer_plan(self.run).playlists[0].entries[0].reason, "Explicitly approved during review")
        self.assertEqual(len(self.destination.additions), 1)

    def test_leave_keeps_plan_without_creating_playlist(self):
        self.destination.catalog = []
        self.assertEqual(self.transfer("--dry-run"), 0)
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="leave"), patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["resume", str(self.run)]), 2)
        self.assertEqual(self.destination.creations, 0)

    def test_saved_manual_mapping_is_reused(self):
        self.destination.catalog[0]["title"] = "Winter Sun (Live)"
        choices = self.root / "state" / "choices.json"
        choices.parent.mkdir()
        choices.write_text(json.dumps({f"spotify:{FIXTURE['spotify']['id']}:ytmusic": FIXTURE["ytmusic"]["videoId"]}))
        self.assertEqual(self.transfer(), 0)
        entry = load_transfer_plan(self.run).playlists[0].entries[0]
        self.assertIn("Previously approved", entry.reason)
        self.assertEqual(entry.destination_id, FIXTURE["ytmusic"]["videoId"])

    def test_unavailable_saved_mapping_returns_to_review(self):
        choices = self.root / "state" / "choices.json"
        choices.parent.mkdir()
        choices.write_text(json.dumps({f"spotify:{FIXTURE['spotify']['id']}:ytmusic": "gone"}))
        with patch.object(self.destination, "get_track", side_effect=ValueError("Unavailable")):
            self.assertEqual(self.transfer(), 2)
        self.assertEqual(load_transfer_plan(self.run).playlists[0].entries[0].decision, "unresolved")
        self.assertEqual(self.destination.creations, 0)

    def test_review_can_override_saved_mapping(self):
        alternative = copy.deepcopy(FIXTURE["ytmusic"])
        alternative.update(videoId="alternative", title="Winter Sun (Live)")
        self.destination.catalog.append(alternative)
        choices = self.root / "state" / "choices.json"
        choices.parent.mkdir()
        choices.write_text(json.dumps({f"spotify:{FIXTURE['spotify']['id']}:ytmusic": "alternative"}))
        self.assertEqual(self.transfer("--review", "--dry-run"), 0)
        self.assertEqual(load_transfer_plan(self.run).playlists[0].entries[0].destination_id, FIXTURE["ytmusic"]["videoId"])
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="2"), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["resume", str(self.run), "--review"]), 0)
        self.assertEqual(load_transfer_plan(self.run).playlists[0].entries[0].destination_id, "alternative")

    def test_finished_with_omissions_is_successful_execution(self):
        self.destination.catalog = []
        self.assertEqual(self.transfer("--dry-run"), 0)
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="skip"), patch("sys.stdout", new=io.StringIO()) as output:
            self.assertEqual(main(["resume", str(self.run)]), 0)
            self.assertIn("0 of 1", output.getvalue())
        report = json.loads((self.run / "report.json").read_text())
        self.assertEqual(report["status"], "finished")
        self.assertEqual(report["playlists"][0]["skipped"], 1)


class EditingTests(unittest.TestCase):
    def test_spotify_remove_targets_selected_occurrence_with_snapshot(self):
        from unittest.mock import Mock
        client = Mock(service="spotify")
        client.get_playlist.return_value = {"snapshot_id": "current"}
        client.get_playlist_tracks.return_value = [{"item": {"uri": "spotify:track:same"}}] * 3
        with patch("music_api.cli._client", return_value=client), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["playlist", "--service", "spotify", "remove", "mix", "1"]), 0)
        client.remove_tracks.assert_called_once_with("mix", [{"uri": "spotify:track:same", "positions": [1]}], snapshot_id="current")

    def test_youtube_remove_uses_unique_occurrence(self):
        from unittest.mock import Mock
        client = Mock(service="ytmusic")
        tracks = [{"videoId": "same", "setVideoId": "first"}, {"videoId": "same", "setVideoId": "second"}]
        client.get_playlist_tracks.return_value = tracks
        with patch("music_api.cli._client", return_value=client), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["playlist", "--service", "ytmusic", "remove", "mix", "1"]), 0)
        client.remove_tracks.assert_called_once_with("mix", [tracks[1]])

    def test_invalid_edit_positions_do_not_write(self):
        from unittest.mock import Mock
        client = Mock(service="spotify")
        client.get_playlist_tracks.return_value = []
        with patch("music_api.cli._client", return_value=client), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["playlist", "--service", "spotify", "move", "mix", "0", "--before", "0"]), 1)
        client.move_track.assert_not_called()

    def test_spotify_move_stops_if_playlist_changes_during_collection(self):
        from unittest.mock import Mock
        client = Mock(service="spotify")
        client.get_playlist.side_effect = [{"snapshot_id": "before"}, {"snapshot_id": "after"}]
        client.get_playlist_tracks.return_value = [{"item": {"uri": "spotify:track:same"}}] * 2
        with patch("music_api.cli._client", return_value=client), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["playlist", "--service", "spotify", "move", "mix", "0", "--before", "2"]), 1)
        client.move_track.assert_not_called()

    def test_addition_batches_preserve_duplicates(self):
        from unittest.mock import Mock
        client = Mock(service="ytmusic")
        with patch("music_api.cli._client", return_value=client), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["playlist", "--service", "ytmusic", "add", "mix", *(["same"] * 101)]), 0)
        self.assertEqual([len(call.args[1]) for call in client.add_tracks.call_args_list], [100, 1])
        self.assertTrue(all(call.kwargs["duplicates"] for call in client.add_tracks.call_args_list))
