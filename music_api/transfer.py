"""Plan, review, and execute ordered playlist copies."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spotipy.exceptions import SpotifyException

from .identifiers import identifier
from .spotify_client import SpotifyClient
from .ytmusic_client import YTMusicClient

Client = SpotifyClient | YTMusicClient
STATE_DIR = Path.home() / ".local" / "state" / "music-api"


@dataclass
class Track:
    id: str
    title: str
    artists: list[str]
    album: str = ""
    duration: float | None = None
    explicit: bool | None = None
    kind: str = "song"
    available: bool = True


@dataclass
class Match:
    position: int
    source: Track
    candidates: list[Track] = field(default_factory=list)
    decision: str = "unresolved"
    destination_id: str | None = None
    reason: str = ""


@dataclass
class PlaylistPlan:
    source_id: str
    title: str
    description: str
    entries: list[Match]


@dataclass
class TransferPlan:
    source_service: str
    destination_service: str
    playlists: list[PlaylistPlan]


@dataclass
class PlaylistReport:
    source_id: str
    destination_id: str | None
    total: int
    copied: int
    skipped: int
    unsupported: int
    unresolved: int
    verified: bool


@dataclass
class TransferReport:
    status: str
    playlists: list[PlaylistReport]


class TransferInterrupted(RuntimeError):
    """A saved transfer needs recovery before further writes."""


def normalize_track(raw: dict[str, Any] | None, service: str) -> Track:
    if not raw:
        return Track("", "Unavailable entry", [], kind="unknown", available=False)
    if service == "spotify":
        kind = "local" if raw.get("is_local") else ("song" if raw.get("type") == "track" else raw.get("type", "unknown"))
        return Track(raw.get("id") or "", raw.get("name") or "Unknown title",
                     [a["name"] for a in raw.get("artists", [])],
                     (raw.get("album") or {}).get("name", ""),
                     raw["duration_ms"] / 1000 if raw.get("duration_ms") is not None else None,
                     raw.get("explicit"), kind, raw.get("is_playable") is not False)
    video_type = raw.get("videoType", "")
    kind = "song"
    if raw.get("resultType") in {"episode", "podcast"}:
        kind = "episode"
    elif video_type == "MUSIC_VIDEO_TYPE_PRIVATELY_OWNED_TRACK":
        kind = "upload"
    elif raw.get("resultType") == "video" or video_type in {"MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC"}:
        kind = "video"
    return Track(raw.get("videoId") or "", raw.get("title") or "Unknown title",
                 [a["name"] for a in raw.get("artists", [])],
                 (raw.get("album") or {}).get("name", ""), raw.get("duration_seconds"),
                 raw.get("isExplicit"), kind, raw.get("isAvailable") is not False)


def _text(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _versions(track: Track) -> set[str]:
    return set(re.findall(r"\b(?:live|remix|remaster(?:ed)?|acoustic|instrumental|karaoke|radio edit|mono|stereo)\b",
                          _text(track.title + " " + track.album)))


def strong_match(source: Track, candidate: Track) -> bool:
    return bool(
        source.kind == candidate.kind == "song" and source.available and candidate.available
        and source.artists and candidate.artists
        and all(_text(a) for a in source.artists + candidate.artists)
        and _text(source.title) == _text(candidate.title)
        and {_text(a) for a in source.artists} == {_text(a) for a in candidate.artists}
        and _versions(source) == _versions(candidate)
        and (not source.album or not candidate.album or _text(source.album) == _text(candidate.album))
        and source.duration is not None and candidate.duration is not None
        and abs(source.duration - candidate.duration) <= 5
        and source.explicit is not None and candidate.explicit is not None
        and source.explicit == candidate.explicit
    )


def search_candidates(client: Client, source: Track, videos: bool = False) -> list[Track]:
    query = f"{source.title} {' '.join(source.artists)}"
    if client.service == "spotify":
        results = client.search(query, limit=10)["tracks"]["items"]
    else:
        results = client.search(query, filter="videos" if videos else "songs", limit=10)
    candidates = {}
    for raw in results:
        track = normalize_track(raw, client.service)
        if track.id and track.available:
            candidates.setdefault(track.id, track)
    return list(candidates.values())


def _capture(client: Client, playlist_id: str) -> PlaylistPlan:
    metadata = client.get_playlist(playlist_id)
    if client.service == "spotify":
        if not isinstance(metadata.get("items"), dict):
            raise ValueError(f"Spotify playlist contents are inaccessible: {playlist_id}")
        snapshot = metadata["snapshot_id"]
        items = client.get_playlist_tracks(playlist_id)
        after = client.get_playlist(playlist_id)
        if after["snapshot_id"] != snapshot:
            raise ValueError(f"Source playlist changed during collection: {playlist_id}")
        total = metadata["items"]["total"]
        tracks = [normalize_track(item.get("item"), client.service) for item in items]
        title = metadata["name"]
    else:
        items = metadata["tracks"]
        total = metadata.get("trackCount")
        tracks = [normalize_track(item, client.service) for item in items]
        title = metadata["title"]
    if total is None or int(total) != len(tracks):
        raise ValueError(f"Cannot establish source completeness for {playlist_id}: fetched {len(tracks)}, advertised {total}")
    return PlaylistPlan(playlist_id, title, metadata.get("description") or "",
                        [Match(i, track) for i, track in enumerate(tracks)])


def plan_transfer(source_client: Client, destination_client: Client, playlist_ids: list[str]) -> TransferPlan:
    if source_client.service == destination_client.service:
        raise ValueError("Source and destination services must differ")
    if not playlist_ids:
        raise ValueError("Select at least one playlist")
    ids = dict.fromkeys(identifier(p, source_client.service) for p in playlist_ids)
    playlists = [_capture(source_client, p) for p in ids]
    matches = {}
    for playlist in playlists:
        for entry in playlist.entries:
            source = entry.source
            if source.kind not in {"song", "video"} or not source.id:
                entry.reason = "Unsupported source entry; select a replacement or explicitly skip"
                continue
            key = source.id
            if key not in matches:
                candidates = search_candidates(destination_client, source)
                strong = [t for t in candidates if strong_match(source, t)]
                matches[key] = candidates, strong[0].id if len(strong) == 1 else None
            entry.candidates, entry.destination_id = matches[key]
            if entry.destination_id:
                entry.decision = "selected"
                entry.reason = "Unique match on title, artists, version, explicitness, and duration"
            else:
                entry.reason = "Recording uncertain or multiple candidates" if entry.candidates else "No destination song found"
    return TransferPlan(source_client.service, destination_client.service, playlists)


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def _run_lock(run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (run_dir / "apply.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TransferInterrupted("Another process is using this transfer run") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _validate_plan(plan: TransferPlan) -> None:
    if {plan.source_service, plan.destination_service} != {"spotify", "ytmusic"} or not plan.playlists:
        raise ValueError("Invalid transfer services or empty plan")
    seen = set()
    for playlist in plan.playlists:
        identifier(playlist.source_id, plan.source_service)
        if playlist.source_id in seen:
            raise ValueError("Duplicate source playlist in saved plan")
        seen.add(playlist.source_id)
        for position, entry in enumerate(playlist.entries):
            if entry.position != position or entry.decision not in {"unresolved", "selected", "skipped"}:
                raise ValueError("Invalid occurrence or decision in saved plan")
            if entry.decision == "selected":
                identifier(entry.destination_id or "", plan.destination_service, "track")
            elif entry.destination_id is not None:
                raise ValueError("Only selected entries may have a destination ID")


def save_transfer_plan(plan: TransferPlan, run_dir: str | Path) -> None:
    _validate_plan(plan)
    run_dir = Path(run_dir).expanduser()
    with _run_lock(run_dir):
        progress_path = run_dir / "progress.json"
        if progress_path.exists():
            raise ValueError("An applied plan is frozen; create a new run to change its decisions")
        _atomic_json(run_dir / "plan.json", asdict(plan))


def load_transfer_plan(run_dir: str | Path) -> TransferPlan:
    data = json.loads((Path(run_dir).expanduser() / "plan.json").read_text())
    playlists = []
    for raw in data["playlists"]:
        entries = [Match(**{**e, "source": Track(**e["source"]),
                           "candidates": [Track(**t) for t in e["candidates"]]}) for e in raw["entries"]]
        playlists.append(PlaylistPlan(**{**raw, "entries": entries}))
    plan = TransferPlan(data["source_service"], data["destination_service"], playlists)
    _validate_plan(plan)
    return plan


def _destination_tracks(client: Client, playlist_id: str) -> list[Track]:
    try:
        return [entry.source for entry in _capture(client, playlist_id).entries]
    except ValueError as exc:
        raise TransferInterrupted(f"Cannot verify destination contents: {exc}") from exc


def _same_sequence(client: Client, actual: list[Track], expected: list[str]) -> bool:
    if len(actual) != len(expected):
        return False
    for track, expected_id in zip(actual, expected):
        if track.id == expected_id:
            continue
        if client.service != "spotify" or client.get_track(expected_id).get("id") != track.id:
            return False
    return True


def _report(plan: TransferPlan, progress: dict, status: str) -> TransferReport:
    reports = []
    for playlist in plan.playlists:
        state = progress["playlists"][playlist.source_id]
        unsupported = sum(e.decision == "skipped" and (e.source.kind not in {"song", "video"} or not e.source.id) for e in playlist.entries)
        skipped = sum(e.decision == "skipped" for e in playlist.entries) - unsupported
        reports.append(PlaylistReport(playlist.source_id, state["destination_id"], len(playlist.entries),
            state["confirmed"], skipped, unsupported, sum(e.decision == "unresolved" for e in playlist.entries), state["verified"] and status == "finished"))
    return TransferReport(status, reports)


def apply_transfer(plan: TransferPlan, destination_client: Client, run_dir: str | Path,
                   *, destination_playlist: str | None = None) -> TransferReport:
    """Apply a frozen plan; resume confirmed writes and stop on uncertain outcomes."""
    _validate_plan(plan)
    if destination_client.service != plan.destination_service:
        raise ValueError("Destination client does not match the saved plan")
    if any(e.decision == "unresolved" for p in plan.playlists for e in p.entries):
        raise ValueError("Resolve every entry or explicitly skip it before applying")
    run_dir = Path(run_dir).expanduser()
    with _run_lock(run_dir):
        plan_path = run_dir / "plan.json"
        if plan_path.exists() and json.loads(plan_path.read_text()) != asdict(plan):
            raise ValueError("Saved plan differs; save reviewed decisions before applying")
        if not plan_path.exists():
            _atomic_json(plan_path, asdict(plan))
        progress_path = run_dir / "progress.json"
        plan_hash = hashlib.sha256(json.dumps(asdict(plan), sort_keys=True).encode()).hexdigest()
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"plan_hash": plan_hash, "playlists": {
            p.source_id: {"destination_id": None, "confirmed": 0, "pending": None, "verified": False} for p in plan.playlists}}
        if progress["plan_hash"] != plan_hash or set(progress["playlists"]) != {p.source_id for p in plan.playlists}:
            raise ValueError("Progress does not belong to this plan")
        for playlist in plan.playlists:
            state = progress["playlists"][playlist.source_id]
            count = sum(e.decision == "selected" for e in playlist.entries)
            if type(state["confirmed"]) is not int or not 0 <= state["confirmed"] <= count:
                raise ValueError("Invalid confirmed progress")
            if state["destination_id"]:
                identifier(state["destination_id"], plan.destination_service)
            elif state["confirmed"]:
                raise ValueError("Confirmed progress requires a destination playlist")
            pending = state["pending"]
            if pending is not None and pending != {"kind": "create"}:
                if not state["destination_id"] or pending.get("kind") != "add" or pending.get("start") != state["confirmed"] or type(pending.get("end")) is not int or not state["confirmed"] < pending["end"] <= count:
                    raise ValueError("Invalid pending operation")
        recovery = [p for p in plan.playlists if progress["playlists"][p.source_id]["pending"] == {"kind": "create"}]
        if destination_playlist and len(recovery) != 1:
            raise ValueError("Specify a recovery playlist only for one uncertain creation")
        try:
            for playlist in plan.playlists:
                state = progress["playlists"][playlist.source_id]
                expected = [e.destination_id for e in playlist.entries if e.decision == "selected"]
                if state["pending"] == {"kind": "create"}:
                    if not destination_playlist:
                        raise TransferInterrupted("Playlist creation outcome is unknown. Locate the created playlist and use resume --destination-playlist LINK")
                    recovered_id = identifier(destination_playlist, plan.destination_service)
                    actual = _destination_tracks(destination_client, recovered_id)
                    if not _same_sequence(destination_client, actual, expected[:len(actual)]):
                        raise TransferInterrupted("Recovery playlist does not contain a prefix of the approved transfer")
                    state.update(destination_id=recovered_id, confirmed=len(actual), pending=None)
                    _atomic_json(progress_path, progress)
                if not state["destination_id"]:
                    state["pending"] = {"kind": "create"}
                    _atomic_json(progress_path, progress)
                    try:
                        new_id = destination_client.create_playlist(playlist.title, playlist.description)
                    except SpotifyException as exc:
                        if 400 <= exc.http_status < 500 and exc.http_status != 408:
                            state["pending"] = None
                            _atomic_json(progress_path, progress)
                        raise
                    state.update(destination_id=identifier(new_id, plan.destination_service), pending=None)
                    _atomic_json(progress_path, progress)
                actual = _destination_tracks(destination_client, state["destination_id"])
                pending = state["pending"]
                if pending:
                    if pending.get("kind") != "add" or pending["start"] != state["confirmed"] or not state["confirmed"] < pending["end"] <= len(expected):
                        raise ValueError("Invalid pending operation")
                    # shortcut: uncertain partial batches require manual completion, add recovery controls if frequent.
                    if not _same_sequence(destination_client, actual, expected[:pending["end"]]):
                        raise TransferInterrupted("Pending addition is not fully visible. Do not replay it; check the destination, complete the approved batch manually if necessary, then resume")
                    state.update(confirmed=pending["end"], pending=None)
                    _atomic_json(progress_path, progress)
                if not _same_sequence(destination_client, actual, expected[:state["confirmed"]]):
                    raise TransferInterrupted("Destination differs from confirmed progress; check for edits or stale reads before resuming")
                while state["confirmed"] < len(expected):
                    start = state["confirmed"]
                    end = min(start + 100, len(expected))
                    state.update(pending={"kind": "add", "start": start, "end": end}, verified=False)
                    _atomic_json(progress_path, progress)
                    try:
                        if destination_client.service == "ytmusic":
                            destination_client.add_tracks(state["destination_id"], expected[start:end], duplicates=True)
                        else:
                            destination_client.add_tracks(state["destination_id"], expected[start:end])
                    except SpotifyException as exc:
                        if 400 <= exc.http_status < 500 and exc.http_status != 408:
                            state["pending"] = None
                            _atomic_json(progress_path, progress)
                        raise
                    state.update(confirmed=end, pending=None)
                    _atomic_json(progress_path, progress)
                actual = _destination_tracks(destination_client, state["destination_id"])
                if not _same_sequence(destination_client, actual, expected):
                    state["verified"] = False
                    raise TransferInterrupted("Final verification differs from the approved sequence; resume after checking the destination")
                state["verified"] = True
                _atomic_json(progress_path, progress)
        except Exception:
            report = _report(plan, progress, "interrupted")
            _atomic_json(run_dir / "report.json", asdict(report))
            raise
        report = _report(plan, progress, "finished")
        _atomic_json(run_dir / "report.json", asdict(report))
        return report
