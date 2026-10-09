# music-api

Python and CLI tools for browsing Spotify and YouTube Music libraries, editing
playlists, and copying selected playlists between the two services.

Playlist copies preserve order and repeated songs. Uncertain recordings require
review. A transfer creates a new private playlist and records progress locally.

## Install

Use Python 3.10 or newer:

```bash
python -m pip install -e .
# Equivalent:
python -m pip install -r requirements.txt
```

The tested client versions are declared in `pyproject.toml`. The transfer lock
uses `fcntl`, so this version runs on macOS and Linux.

## Authentication

Copy `.env.example` to `.env` and fill in credentials. Configuration is loaded
when a client is constructed, not when the package is imported. Environment
variables override the selected file. Relative OAuth and Spotify cache paths
are resolved beside the file. Without `--env-file`, the CLI uses `.env` in the
current directory.

### Spotify

Create an app in the [developer dashboard](https://developer.spotify.com/dashboard)
and register `http://127.0.0.1:8888/callback` exactly. Set `SPOTIFY_CLIENT_ID` and
`SPOTIFY_CLIENT_SECRET`. Spotify opens a browser for authorization and caches the
token in `.spotify_token_cache`. Authorization includes library reads, private
and collaborative playlist reads, and public/private playlist modification.

Spotify development-mode apps require the owner to have Premium. Current
playlist-item endpoints restrict access to playlists the user owns or
collaborates on; being able to list a playlist does not prove its contents are
accessible. Account/app restrictions can differ for existing integrations; see
[Spotify's migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
An access denial stops a transfer before destination creation.

### YouTube Music

Create a Google Cloud project and OAuth client for **TVs and Limited Input
devices**, following the [ytmusicapi OAuth instructions](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html).
Run `ytmusicapi oauth`, follow its prompts, and set:

```dotenv
YTMUSIC_CLIENT_ID=your_google_oauth_client_id
YTMUSIC_CLIENT_SECRET=your_google_oauth_client_secret
YTMUSIC_AUTH_FILE=oauth.json
```

`.env`, OAuth files, and token caches are git-ignored. Keep credentials outside
version control, including when using custom paths. There is no official
YouTube Music API; this package uses `ytmusicapi`.

## Browse and edit

Put `--env-file` before the command and `--service` before playlist subcommands:

```bash
music-api --env-file /path/to/.env playlists --service spotify
music-api playlists --service ytmusic
music-api search --service ytmusic "Winter Sun Example Artist"
music-api library --service spotify songs --limit 100
music-api library --service ytmusic songs --liked --limit 100
music-api library --service ytmusic albums --limit 100
music-api playlist --service spotify show PLAYLIST_ID
music-api playlist --service ytmusic create "A new mix" --description "An evening mix"
music-api playlist --service spotify rename PLAYLIST_ID "A new title"
music-api playlist --service ytmusic add PLAYLIST_ID TRACK_ID TRACK_LINK
music-api playlist --service spotify remove PLAYLIST_ID 0 4
music-api playlist --service ytmusic move PLAYLIST_ID 3 --before 0
```

Positions are zero-based occurrences, not song IDs. Removal targets the selected
occurrences; repeated songs at other positions are retained. `move --before`
uses an index in the current playlist, or its length to move to the end. Editing
requires an owned or writable playlist. Direct editing commands do not have
transfer recovery; after an uncertain write, inspect the playlist before
repeating the command.

Playlist listings fetch all items unless `--limit` is supplied. Library browsing
displays up to 100 items by default. Spotify library songs are liked songs;
YouTube library songs and liked songs are distinct. `playlist show` lists all
occurrences. Search returns up to ten results by default.

`python -m music_api` runs the same CLI. `python examples/demo.py --spotify`
and `--ytmusic` demonstrate read-only usage.

## Copy playlists

Preview one or several complete playlists:

```bash
music-api transfer --from spotify --to ytmusic \
  --playlist SPOTIFY_PLAYLIST_LINK --playlist ANOTHER_PLAYLIST_ID --dry-run
```

The command prints a saved run directory. The source collection and searches
perform no playlist mutations. Every source occurrence, destination candidate,
and decision is in `plan.json`.

Review and apply the saved plan:

```bash
music-api resume /path/to/saved/run
```

Or copy directly:

```bash
music-api transfer --from ytmusic --to spotify --playlist YOUTUBE_PLAYLIST_LINK
```

A unique match on title, artists, recording version, explicitness, and duration
within five seconds is selected automatically. Conflicting album information
requires review. Metadata matching is a heuristic, not audio verification.
Ambiguous, unavailable, and unsupported entries remain visible. In the terminal,
choose a displayed number, `link ID_OR_URL`, `videos` for a YouTube video search,
`skip`, or `leave` to continue later. Video replacements require explicit review.
Leaving review or pressing Ctrl-C retains the decisions already saved.

Approved replacements are reused across playlists. `transfer --review` or
`resume --review` reviews selected matches too. Source occurrences remain
separate even when they share a matching decision. Unavailable saved matches
return to review. Noninteractive execution
stops with exit code 2 when review is required; it never silently skips entries.
A dry run can leave unresolved entries and exits successfully when its plan is
saved.

Once writing begins, the plan is frozen. Source edits after collection are not
included in that run. Copies retain titles and descriptions, use private
visibility, and append in batches of up to 100. Repeated songs are preserved.
An empty source creates an empty destination; explicitly skipping every entry
also produces an empty copy.

Final verification checks order and multiplicity of the approved destination
sequence. A result such as `Finished: 97 of 100 occurrences copied; 3 intentionally
omitted; verified=True` means the approved copy completed, with reported omissions.
It does not mean every source track was transferred. Other failures exit with
code 1. Reports are saved in `report.json`.

## Resume and recovery

```bash
music-api resume /path/to/saved/run
```

Confirmed additions are never replayed. If an interrupted batch is fully visible
in the destination, resume adopts it and continues. A conflicting edit or stale
read stops the run. When a timed-out addition is absent or only partially visible,
its outcome is uncertain: inspect the destination and the pending range in
`progress.json`, wait for any delayed result, and manually complete the approved
batch if needed before resuming. The tool will not guess and repeat it.

If playlist creation timed out, locate the new private playlist in the service
and bind it explicitly:

```bash
music-api resume /path/to/saved/run --destination-playlist CREATED_PLAYLIST_LINK
```

The supplied playlist must contain an approved prefix. It is never selected by
name. If no creation can be found, the run remains unresolved; a fresh run may
create another copy if the first request eventually succeeded.

Do not edit a destination while a transfer is running. Final verification stops
on unexplained differences. Spotify can relink track IDs for availability; a
reported difference is accepted only when a track lookup confirms the mapping.

Run files and approved mappings live under `~/.local/state/music-api/`, with
atomic JSON writes and an exclusive per-run lock. Use `transfer --run-dir PATH`
for a chosen new run directory. Run files contain library metadata and should
be treated as personal data; credentials are not stored in them.

## Python API

```python
from pathlib import Path
from music_api import (
    SpotifyClient, YTMusicClient, load_spotify_config, load_ytmusic_config,
    plan_transfer, save_transfer_plan, apply_transfer,
)

credentials = Path("/path/to/.env")
sp = SpotifyClient(load_spotify_config(credentials))
yt = YTMusicClient(load_ytmusic_config(credentials))
plan = plan_transfer(sp, yt, ["SPOTIFY_PLAYLIST_ID"])

# Inspect each entry and resolve uncertain tracks before applying.
for playlist in plan.playlists:
    for entry in playlist.entries:
        print(entry.position, entry.source.title, entry.decision, entry.candidates)

save_transfer_plan(plan, "my-run")
# A reviewed entry uses decision="selected" and destination_id="TRACK_ID";
# an intentional omission uses decision="skipped" and destination_id=None.
# apply_transfer(plan, yt, "my-run")
```

`load_transfer_plan(run_dir)` restores a saved plan. `apply_transfer` has no
terminal prompts, requires every decision to be resolved, and returns a
`TransferReport`. Exceptions leave write intent and confirmed progress on disk.
Resume with the same plan and destination client. Terminal review stays
inside the CLI; Python callers can edit the dataclass decisions before saving.

Client responses retain native service shapes. Spotify playlist occurrences use
`item`; saved-song occurrences use `track`. Spotify `get_playlist` includes only
the first page; `get_playlist_tracks` paginates fully. Both clients support
playlist creation, renaming, additions, removals, movement, and track lookup.
One `add_tracks` call accepts 1–100 tracks. Spotify creations are private; YouTube
`add_tracks(..., duplicates=True)` preserves repeated IDs. Spotify removals take
URI/positions dictionaries and an optional snapshot; YouTube removals take
`videoId`/`setVideoId` occurrence dictionaries.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use fake services and mocked SDK transports. They exercise pagination,
request bodies, matching, explicit omissions, duplicates, batch boundaries,
write interruptions, stale reads, concurrent application, recovery, and CLI dry
runs without connecting to music accounts.

Live matching quality and service writes require separate validation with small
private playlists. Synchronization, playback, entire-library migration,
liked-song writes, and saved-album writes are outside this version.
