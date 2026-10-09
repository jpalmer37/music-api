"""Command-line library browsing, playlist editing, and transfer review."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from requests.exceptions import RequestException
from spotipy.exceptions import SpotifyException
from ytmusicapi.exceptions import YTMusicError

from .config import load_spotify_config, load_ytmusic_config
from .identifiers import identifier, item_link
from .spotify_client import SpotifyClient
from .transfer import (STATE_DIR, _atomic_json, apply_transfer,
                       load_transfer_plan, normalize_track, plan_transfer,
                       save_transfer_plan, search_candidates)
from .ytmusic_client import YTMusicClient


def _client(service: str, env_file: str | None):
    if service == "spotify":
        return SpotifyClient(load_spotify_config(env_file))
    return YTMusicClient(load_ytmusic_config(env_file))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="music-api", description="Browse libraries, edit playlists, and copy playlists between services")
    parser.add_argument("--env-file", help="Credential file (defaults to .env in the current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("playlists", "search", "library", "playlist"):
        command = commands.add_parser(name)
        command.add_argument("--service", choices=("spotify", "ytmusic"), required=True)
        if name == "playlists":
            command.add_argument("--limit", type=int)
        elif name == "search":
            command.add_argument("query")
            command.add_argument("--limit", type=int, default=10)
        elif name == "library":
            command.add_argument("kind", choices=("songs", "albums"))
            command.add_argument("--limit", type=int, default=100, help="Maximum items to display (default: 100)")
            command.add_argument("--liked", action="store_true", help="Read YouTube liked songs rather than saved library songs")
        else:
            actions = command.add_subparsers(dest="action", required=True)
            for action in ("show", "create", "rename", "add", "remove", "move"):
                sub = actions.add_parser(action)
                if action == "create":
                    sub.add_argument("title")
                    sub.add_argument("--description", default="")
                    continue
                sub.add_argument("playlist_id")
                if action == "rename":
                    sub.add_argument("title")
                elif action == "add":
                    sub.add_argument("tracks", nargs="+", help="Track IDs, URIs, or links")
                elif action == "remove":
                    sub.add_argument("positions", type=int, nargs="+", help="Zero-based occurrences to remove")
                elif action == "move":
                    sub.add_argument("position", type=int)
                    sub.add_argument("--before", type=int, required=True, help="Current index to move before; playlist length means the end")
    transfer = commands.add_parser("transfer")
    transfer.add_argument("--from", dest="source", choices=("spotify", "ytmusic"), required=True)
    transfer.add_argument("--to", dest="destination", choices=("spotify", "ytmusic"), required=True)
    transfer.add_argument("--playlist", action="append", required=True)
    transfer.add_argument("--dry-run", action="store_true")
    transfer.add_argument("--review", action="store_true", help="Review automatic and saved matches too")
    transfer.add_argument("--run-dir", type=Path, help="New run directory (default: ~/.local/state/music-api/runs/UUID)")
    resume = commands.add_parser("resume")
    resume.add_argument("run_dir", type=Path)
    resume.add_argument("--destination-playlist", help="Explicit playlist link or ID for recovering an uncertain creation")
    resume.add_argument("--review", action="store_true", help="Review selected matches before the run starts writing")
    return parser


def _choice_key(plan, entry) -> str:
    return f"{plan.source_service}:{entry.source.id}:{plan.destination_service}"


def _load_choices(plan, destination, choices: dict) -> None:
    checked = {}
    for playlist in plan.playlists:
        for entry in playlist.entries:
            key = _choice_key(plan, entry)
            if not entry.source.id or key not in choices:
                continue
            target = identifier(choices[key], plan.destination_service, "track")
            if target not in checked:
                try:
                    checked[target] = normalize_track(destination.get_track(target), plan.destination_service)
                except ValueError:
                    checked[target] = None
                except SpotifyException as exc:
                    if exc.http_status != 404:
                        raise
                    checked[target] = None
            track = checked[target]
            if track is None or not track.available or not track.id:
                entry.decision, entry.destination_id = "unresolved", None
                entry.reason = "Saved destination is unavailable"
                continue
            if track.id not in {c.id for c in entry.candidates}:
                entry.candidates.append(track)
            entry.decision, entry.destination_id = "selected", track.id
            entry.reason = "Previously approved mapping; use --review to override"


def _review(plan, destination, run_dir: Path, choices: dict, review_all: bool = False) -> bool:
    if not sys.stdin.isatty():
        return not review_all and all(e.decision != "unresolved" for p in plan.playlists for e in p.entries)
    decisions = {}
    for playlist in plan.playlists:
        for entry in playlist.entries:
            key = _choice_key(plan, entry)
            if entry.source.id and key in decisions:
                entry.decision, entry.destination_id, entry.reason = decisions[key]
                save_transfer_plan(plan, run_dir)
                continue
            if entry.decision != "unresolved" and not review_all:
                continue
            while True:
                source = entry.source
                print(f"\n{playlist.title}, occurrence {entry.position}: {source.title} — {', '.join(source.artists)}")
                print(f"  Source album: {source.album or '?'}; duration: {source.duration}; explicit: {source.explicit}")
                print(f"  {entry.reason}")
                for index, candidate in enumerate(entry.candidates, 1):
                    print(f"  {index}: {candidate.title} — {', '.join(candidate.artists)} | {candidate.album or '?'} | {candidate.duration}s | explicit={candidate.explicit}")
                    print(f"     {item_link(plan.destination_service, candidate.id)}")
                try:
                    choice = input("Number, link ID_OR_URL, videos, skip, or leave: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nReview saved; use resume to continue.", file=sys.stderr)
                    return False
                if choice == "leave":
                    save_transfer_plan(plan, run_dir)
                    return False
                if choice == "videos":
                    if destination.service != "ytmusic":
                        print("Video searches are available only on YouTube Music")
                        continue
                    entry.candidates = search_candidates(destination, source, videos=True)
                    continue
                if choice == "skip":
                    entry.decision, entry.destination_id = "skipped", None
                    entry.reason = "Explicitly skipped during review"
                    choices.pop(key, None)
                else:
                    try:
                        if choice.startswith("link "):
                            target = identifier(choice[5:], plan.destination_service, "track")
                            candidate = normalize_track(destination.get_track(target), plan.destination_service)
                        else:
                            index = int(choice)
                            if not 1 <= index <= len(entry.candidates):
                                raise ValueError("Choose a displayed candidate")
                            candidate = entry.candidates[index - 1]
                        if not candidate.available or not candidate.id:
                            raise ValueError("Destination is unavailable")
                    except ValueError as exc:
                        print(exc)
                        continue
                    entry.decision, entry.destination_id = "selected", candidate.id
                    entry.reason = "Explicitly approved during review"
                    if source.id:
                        choices[key] = candidate.id
                if source.id:
                    decisions[key] = entry.decision, entry.destination_id, entry.reason
                save_transfer_plan(plan, run_dir)
                _atomic_json(STATE_DIR / "choices.json", choices)
                break
    return True


def _print_plan(plan, run_dir: Path) -> None:
    print(f"Saved run: {run_dir}")
    for playlist in plan.playlists:
        selected = sum(e.decision == "selected" for e in playlist.entries)
        unresolved = sum(e.decision == "unresolved" for e in playlist.entries)
        print(f"{playlist.title}: {len(playlist.entries)} occurrences, {selected} selected, {unresolved} need review")
        for entry in playlist.entries:
            if entry.decision == "unresolved":
                print(f"  {entry.position}: {entry.source.title}: {entry.reason}")
    print(f"Plan and candidate links: {run_dir / 'plan.json'}")


def _transfers(args) -> int:
    choices_path = STATE_DIR / "choices.json"
    choices = json.loads(choices_path.read_text()) if choices_path.exists() else {}
    if args.command == "transfer":
        if args.source == args.destination:
            raise ValueError("Select different source and destination services")
        run_dir = (args.run_dir or STATE_DIR / "runs" / str(uuid.uuid4())).expanduser().resolve()
        if (run_dir / "plan.json").exists() or (run_dir / "progress.json").exists():
            raise ValueError("Run already exists; use resume or select a new run directory")
        source = _client(args.source, args.env_file)
        destination = _client(args.destination, args.env_file)
        plan = plan_transfer(source, destination, args.playlist)
        save_transfer_plan(plan, run_dir)
        if not args.review:
            _load_choices(plan, destination, choices)
        save_transfer_plan(plan, run_dir)
        _print_plan(plan, run_dir)
        if args.dry_run:
            return 0
    else:
        run_dir = args.run_dir.expanduser().resolve()
        plan = load_transfer_plan(run_dir)
        destination = _client(plan.destination_service, args.env_file)
        _print_plan(plan, run_dir)
    progress_path = run_dir / "progress.json"
    started = progress_path.exists()
    if started and args.review:
        raise ValueError("Applied plans are frozen; --review is available only before writing starts")
    if not started and not _review(plan, destination, run_dir, choices, args.review):
        print(f"Review required. Run music-api resume {run_dir} in an interactive terminal.", file=sys.stderr)
        return 2
    recovery = args.destination_playlist if args.command == "resume" else None
    report = apply_transfer(plan, destination, run_dir, destination_playlist=recovery)
    for playlist in report.playlists:
        omissions = playlist.skipped + playlist.unsupported
        print(f"Finished: {playlist.copied} of {playlist.total} occurrences copied; {omissions} intentionally omitted; verified={playlist.verified}")
        print(item_link(plan.destination_service, playlist.destination_id, "playlist"))
    return 0


def _playlist(args, client) -> None:
    if args.action == "create":
        print(item_link(client.service, client.create_playlist(args.title, args.description), "playlist"))
        return
    playlist_id = identifier(args.playlist_id, client.service)
    if args.action == "show":
        metadata = client.get_playlist(playlist_id)
        print(json.dumps({k: v for k, v in metadata.items() if k not in {"items", "tracks"}}, indent=2, ensure_ascii=False))
        tracks = client.get_playlist_tracks(playlist_id)
        for index, raw in enumerate(tracks):
            track = normalize_track(raw.get("item") if client.service == "spotify" else raw, client.service)
            print(f"{index}: {track.title} — {', '.join(track.artists)} [{track.id}]")
    elif args.action == "rename":
        client.rename_playlist(playlist_id, args.title)
        print("Renamed playlist")
    elif args.action == "add":
        tracks = [identifier(t, client.service, "track") for t in args.tracks]
        for offset in range(0, len(tracks), 100):
            if client.service == "ytmusic":
                client.add_tracks(playlist_id, tracks[offset:offset + 100], duplicates=True)
            else:
                client.add_tracks(playlist_id, tracks[offset:offset + 100])
        print(f"Added {len(tracks)} occurrences")
    elif args.action == "remove":
        metadata = client.get_playlist(playlist_id)
        tracks = client.get_playlist_tracks(playlist_id)
        positions = sorted(set(args.positions))
        if len(positions) > 100 or any(i < 0 or i >= len(tracks) for i in positions):
            raise ValueError("Choose up to 100 valid zero-based positions")
        if client.service == "spotify":
            if metadata["snapshot_id"] != client.get_playlist(playlist_id)["snapshot_id"]:
                raise ValueError("Playlist changed during collection; try again")
            selected = [{"uri": tracks[i]["item"]["uri"], "positions": [i]} for i in positions]
            client.remove_tracks(playlist_id, selected, snapshot_id=metadata["snapshot_id"])
        else:
            client.remove_tracks(playlist_id, [tracks[i] for i in positions])
        print(f"Removed {len(positions)} occurrences")
    else:
        metadata = client.get_playlist(playlist_id) if client.service == "spotify" else None
        tracks = client.get_playlist_tracks(playlist_id)
        if not 0 <= args.position < len(tracks) or not 0 <= args.before <= len(tracks):
            raise ValueError("Position outside the playlist")
        if client.service == "spotify":
            if metadata["snapshot_id"] != client.get_playlist(playlist_id)["snapshot_id"]:
                raise ValueError("Playlist changed during collection; try again")
            client.move_track(playlist_id, args.position, args.before, snapshot_id=metadata["snapshot_id"])
        else:
            client.move_track(playlist_id, args.position, args.before)
        print("Moved occurrence")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"transfer", "resume"}:
            return _transfers(args)
        client = _client(args.service, args.env_file)
        if args.command == "playlist":
            _playlist(args, client)
        elif args.command == "playlists":
            for playlist in client.list_playlists(args.limit):
                title = playlist["name"] if client.service == "spotify" else playlist["title"]
                pid = playlist["id"] if client.service == "spotify" else playlist["playlistId"]
                print(f"{title}\t{pid}")
        elif args.command == "search":
            print(json.dumps(client.search(args.query, limit=args.limit), indent=2, ensure_ascii=False))
        else:
            if args.limit < 1:
                raise ValueError("Library limit must be positive")
            if args.kind == "albums":
                items = client.get_saved_albums(args.limit) if client.service == "spotify" else client.get_library_albums(args.limit)
            elif client.service == "spotify":
                items = client.get_saved_tracks(args.limit)
            else:
                items = client.get_liked_songs(args.limit) if args.liked else client.get_library_songs(args.limit)
            print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError, RequestException, SpotifyException, YTMusicError) as exc:
        print(f"music-api: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
