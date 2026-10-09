import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from music_api.transfer import (Match, PlaylistPlan, Track, TransferInterrupted, TransferPlan,
    _run_lock, apply_transfer, load_transfer_plan, normalize_track, plan_transfer,
    save_transfer_plan, strong_match)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


class FakeClient:
    def __init__(self, service, entries=None):
        self.service = service
        self.catalog = [copy.deepcopy(FIXTURE[service])]
        self.playlists = {"source": copy.deepcopy(entries or [])}
        self.creations = 0
        self.additions = []
        self.create_error = None
        self.add_error = None
        self.total_override = None
        self.snapshot = "stable"
        self.change_source = False
        self.stale = None
        self.relinks = {}
        self.searches = 0

    def get_playlist(self, pid):
        entries = self.stale if self.stale is not None else self.playlists[pid]
        total = self.total_override if self.total_override is not None else len(entries)
        if self.service == "spotify":
            snapshot = self.snapshot
            if self.change_source:
                self.snapshot += "changed"
            return {"name": "A mix", "description": "Description", "snapshot_id": snapshot, "items": {"total": total}}
        return {"title": "A mix", "description": "Description", "trackCount": total, "tracks": copy.deepcopy(entries)}

    def get_playlist_tracks(self, pid):
        entries = copy.deepcopy(self.stale if self.stale is not None else self.playlists[pid])
        return [{"item": e} for e in entries] if self.service == "spotify" else entries

    def search(self, query, **kwargs):
        self.searches += 1
        tracks = copy.deepcopy(self.catalog)
        return {"tracks": {"items": tracks}} if self.service == "spotify" else tracks

    def get_track(self, tid):
        tid = self.relinks.get(tid, tid)
        key = "id" if self.service == "spotify" else "videoId"
        return copy.deepcopy(next(t for t in self.catalog if t[key] == tid))

    def create_playlist(self, title, description):
        self.creations += 1
        pid = f"copy{self.creations}"
        self.playlists[pid] = []
        if self.create_error:
            raise self.create_error
        return pid

    def add_tracks(self, pid, ids, **kwargs):
        self.additions.append((list(ids), kwargs))
        fault = self.add_error
        if fault == "before":
            raise TimeoutError("request timed out")
        length = len(ids) // 2 if fault == "partial" else len(ids)
        self.playlists[pid].extend(self.get_track(tid) for tid in ids[:length])
        if fault in {"after", "partial"}:
            raise TimeoutError("response timed out")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.source = FakeClient("spotify", [FIXTURE["spotify"]])
        self.destination = FakeClient("ytmusic")

    def plan(self, count=1):
        self.source.playlists["source"] = [copy.deepcopy(FIXTURE["spotify"]) for _ in range(count)]
        return plan_transfer(self.source, self.destination, ["source"])

    def test_plan_is_read_only_and_reuses_matching_for_duplicates(self):
        plan = self.plan(201)
        self.assertEqual(len(plan.playlists[0].entries), 201)
        self.assertEqual(self.destination.searches, 1)
        self.assertEqual(self.destination.creations, 0)
        self.assertEqual([e.position for e in plan.playlists[0].entries], list(range(201)))

    def test_batches_preserve_order_duplicates_and_resume_does_not_replay(self):
        plan = self.plan(201)
        report = apply_transfer(plan, self.destination, self.run)
        self.assertEqual([len(ids) for ids, _ in self.destination.additions], [100, 100, 1])
        self.assertTrue(all(kwargs["duplicates"] for _, kwargs in self.destination.additions))
        self.assertEqual(report.playlists[0].copied, 201)
        self.assertTrue(report.playlists[0].verified)
        apply_transfer(plan, self.destination, self.run)
        self.assertEqual(len(self.destination.additions), 3)
        self.assertEqual(self.destination.creations, 1)

    def test_reverse_direction(self):
        source = FakeClient("ytmusic", [FIXTURE["ytmusic"], FIXTURE["ytmusic"]])
        destination = FakeClient("spotify")
        report = apply_transfer(plan_transfer(source, destination, ["source"]), destination, self.run)
        self.assertEqual(report.playlists[0].copied, 2)
        self.assertEqual(destination.additions[0][0], ["song1", "song1"])

    def test_uncertain_committed_write_is_reconciled(self):
        plan = self.plan(101)
        self.destination.add_error = "after"
        with self.assertRaises(TimeoutError):
            apply_transfer(plan, self.destination, self.run)
        progress = json.loads((self.run / "progress.json").read_text())["playlists"]["source"]
        self.assertEqual(progress["confirmed"], 0)
        self.assertEqual(progress["pending"]["end"], 100)
        self.destination.add_error = None
        report = apply_transfer(plan, self.destination, self.run)
        self.assertEqual([len(x[0]) for x in self.destination.additions], [100, 1])
        self.assertEqual(report.playlists[0].copied, 101)

    def test_uncertain_absent_and_partial_writes_are_not_replayed(self):
        for fault in ("before", "partial"):
            with self.subTest(fault=fault):
                self.run = Path(self.temp.name) / fault
                self.destination = FakeClient("ytmusic")
                plan = self.plan(4)
                self.destination.add_error = fault
                with self.assertRaises(TimeoutError):
                    apply_transfer(plan, self.destination, self.run)
                self.destination.add_error = None
                with self.assertRaises(TransferInterrupted):
                    apply_transfer(plan, self.destination, self.run)
                self.assertEqual(len(self.destination.additions), 1)

    def test_stale_read_never_reduces_confirmed_progress(self):
        plan = self.plan(2)
        self.destination.stale = []
        with self.assertRaises(TransferInterrupted):
            apply_transfer(plan, self.destination, self.run)
        with self.assertRaises(TransferInterrupted):
            apply_transfer(plan, self.destination, self.run)
        self.assertEqual(len(self.destination.additions), 1)
        self.destination.stale = None
        self.assertTrue(apply_transfer(plan, self.destination, self.run).playlists[0].verified)

    def test_creation_recovery_requires_explicit_matching_playlist(self):
        plan = self.plan()
        self.destination.create_error = TimeoutError("response lost")
        with self.assertRaises(TimeoutError):
            apply_transfer(plan, self.destination, self.run)
        self.destination.create_error = None
        with self.assertRaises(TransferInterrupted):
            apply_transfer(plan, self.destination, self.run)
        report = apply_transfer(plan, self.destination, self.run, destination_playlist="copy1")
        self.assertEqual(self.destination.creations, 1)
        self.assertEqual(report.playlists[0].copied, 1)

    def test_conflicting_edits_and_plan_changes_stop(self):
        plan = self.plan()
        apply_transfer(plan, self.destination, self.run)
        self.destination.playlists["copy1"].append(copy.deepcopy(FIXTURE["ytmusic"]))
        with self.assertRaises(TransferInterrupted):
            apply_transfer(plan, self.destination, self.run)
        plan.playlists[0].title = "Altered"
        with self.assertRaises(ValueError):
            apply_transfer(plan, self.destination, self.run)
        with self.assertRaises(ValueError):
            save_transfer_plan(plan, self.run)

    def test_unresolved_plan_cannot_write(self):
        self.destination.catalog = []
        plan = self.plan()
        with self.assertRaises(ValueError):
            apply_transfer(plan, self.destination, self.run)
        self.assertEqual(self.destination.creations, 0)

    def test_source_count_and_snapshot_checks(self):
        self.source.total_override = 3
        with self.assertRaises(ValueError):
            self.plan()
        self.source.total_override = None
        self.source.change_source = True
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(self.destination.creations, 0)

    def test_inaccessible_source_is_not_empty(self):
        self.source.get_playlist = lambda pid: {"name": "Not accessible"}
        with self.assertRaises(ValueError):
            self.plan()

    def test_explicit_omissions_have_separate_coverage(self):
        plan = self.plan(3)
        plan.playlists[0].entries[1].decision = "skipped"
        plan.playlists[0].entries[1].destination_id = None
        plan.playlists[0].entries[2].source.kind = "local"
        plan.playlists[0].entries[2].decision = "skipped"
        plan.playlists[0].entries[2].destination_id = None
        report = apply_transfer(plan, self.destination, self.run)
        self.assertEqual(report.status, "finished")
        result = report.playlists[0]
        self.assertEqual((result.total, result.copied, result.skipped, result.unsupported), (3, 1, 1, 1))
        self.assertTrue(result.verified)

    def test_empty_playlist(self):
        plan = self.plan(0)
        report = apply_transfer(plan, self.destination, self.run)
        self.assertEqual(report.playlists[0].copied, 0)
        self.assertEqual(self.destination.additions, [])
        self.assertTrue(report.playlists[0].verified)

    def test_saved_plan_round_trip_and_validation(self):
        plan = self.plan()
        save_transfer_plan(plan, self.run)
        self.assertEqual(load_transfer_plan(self.run), plan)
        data = asdict(plan)
        data["playlists"][0]["entries"][0]["position"] = -1
        (self.run / "plan.json").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_transfer_plan(self.run)

    def test_concurrent_application_is_rejected(self):
        with _run_lock(self.run):
            with self.assertRaises(TransferInterrupted):
                apply_transfer(self.plan(), self.destination, self.run)
        self.assertEqual(self.destination.creations, 0)

    def test_provider_evidenced_relinking_and_unexplained_differences(self):
        source = FakeClient("ytmusic", [FIXTURE["ytmusic"]])
        destination = FakeClient("spotify")
        plan = plan_transfer(source, destination, ["source"])
        relink = copy.deepcopy(FIXTURE["spotify"])
        relink["id"] = "relinked"
        destination.catalog.append(relink)
        destination.relinks["song1"] = "relinked"
        report = apply_transfer(plan, destination, self.run)
        self.assertTrue(report.playlists[0].verified)
        destination.relinks = {}
        with self.assertRaises(TransferInterrupted):
            apply_transfer(plan, destination, self.run)


class MatchingTests(unittest.TestCase):
    def test_normalization_and_recording_evidence(self):
        source = normalize_track(FIXTURE["spotify"], "spotify")
        target = normalize_track(FIXTURE["ytmusic"], "ytmusic")
        self.assertTrue(strong_match(source, target))
        target.title = " WINTER—SUN "
        self.assertTrue(strong_match(source, target))
        for field, value in (("title", "Winter Sun (Live)"), ("album", "Daylight (Remastered)"),
                             ("duration", 190), ("explicit", True), ("explicit", None),
                             ("duration", None), ("available", False), ("kind", "video")):
            with self.subTest(field=field, value=value):
                target = normalize_track(FIXTURE["ytmusic"], "ytmusic")
                setattr(target, field, value)
                self.assertFalse(strong_match(source, target))

    def test_competing_candidates_require_review(self):
        source = FakeClient("spotify", [FIXTURE["spotify"]])
        destination = FakeClient("ytmusic")
        second = copy.deepcopy(FIXTURE["ytmusic"])
        second["videoId"] = "another"
        destination.catalog.append(second)
        entry = plan_transfer(source, destination, ["source"]).playlists[0].entries[0]
        self.assertEqual(entry.decision, "unresolved")
        self.assertEqual(len(entry.candidates), 2)

    def test_large_youtube_source_and_unknown_entries(self):
        source = FakeClient("ytmusic", [FIXTURE["ytmusic"]] * 5001)
        destination = FakeClient("spotify")
        plan = plan_transfer(source, destination, ["source"])
        self.assertEqual(len(plan.playlists[0].entries), 5001)
        source = FakeClient("spotify", [None])
        entry = plan_transfer(source, FakeClient("ytmusic"), ["source"]).playlists[0].entries[0]
        self.assertEqual(entry.source.kind, "unknown")
        self.assertEqual(entry.decision, "unresolved")


class PersistenceTests(unittest.TestCase):
    def test_manual_tampering_with_applied_plan_is_rejected(self):
        source = FakeClient("spotify", [FIXTURE["spotify"]])
        destination = FakeClient("ytmusic")
        plan = plan_transfer(source, destination, ["source"])
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            apply_transfer(plan, destination, run)
            data = json.loads((run / "plan.json").read_text())
            data["playlists"][0]["title"] = "Tampered title"
            (run / "plan.json").write_text(json.dumps(data))
            altered = load_transfer_plan(run)
            with self.assertRaises(ValueError):
                apply_transfer(altered, destination, run)
            self.assertEqual(len(destination.additions), 1)

    def test_failed_checkpoint_preserves_write_intent_and_reconciles(self):
        source = FakeClient("spotify", [FIXTURE["spotify"]])
        destination = FakeClient("ytmusic")
        plan = plan_transfer(source, destination, ["source"])
        from music_api.transfer import _atomic_json
        failed = False

        def checkpoint(path, data):
            nonlocal failed
            if path.name == "progress.json" and data["playlists"]["source"]["confirmed"] == 1 and not failed:
                failed = True
                raise OSError("disk write failed")
            _atomic_json(path, data)

        with tempfile.TemporaryDirectory() as temp:
            with patch("music_api.transfer._atomic_json", side_effect=checkpoint):
                with self.assertRaises(OSError):
                    apply_transfer(plan, destination, temp)
            report = apply_transfer(plan, destination, temp)
            self.assertTrue(report.playlists[0].verified)
            self.assertEqual(len(destination.additions), 1)

    def test_recovery_target_with_wrong_contents_is_rejected(self):
        source = FakeClient("spotify", [FIXTURE["spotify"]])
        destination = FakeClient("ytmusic")
        plan = plan_transfer(source, destination, ["source"])
        destination.create_error = TimeoutError()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(TimeoutError):
                apply_transfer(plan, destination, temp)
            wrong = copy.deepcopy(FIXTURE["ytmusic"])
            wrong["videoId"] = "wrong"
            destination.playlists["wrongmix"] = [wrong]
            with self.assertRaises(TransferInterrupted):
                apply_transfer(plan, destination, temp, destination_playlist="wrongmix")
            self.assertEqual(destination.additions, [])

    def test_definitive_spotify_rejection_can_resume_after_fix(self):
        from spotipy.exceptions import SpotifyException
        source = FakeClient("ytmusic", [FIXTURE["ytmusic"]])
        destination = FakeClient("spotify")
        plan = plan_transfer(source, destination, ["source"])
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(destination, "add_tracks", side_effect=SpotifyException(401, -1, "expired")):
                with self.assertRaises(SpotifyException):
                    apply_transfer(plan, destination, temp)
            state = json.loads((Path(temp) / "progress.json").read_text())["playlists"]["source"]
            self.assertIsNone(state["pending"])
            self.assertEqual(state["confirmed"], 0)
            self.assertTrue(apply_transfer(plan, destination, temp).playlists[0].verified)
