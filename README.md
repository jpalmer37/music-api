# music-api

A small Python toolkit for browsing and managing your personal music libraries on
**Spotify** (via [spotipy](https://github.com/spotipy-dev/spotipy)) and
**YouTube Music** (via [ytmusicapi](https://github.com/sigma67/ytmusicapi)).

## Install

```bash
pip install -e .
# or: pip install -r requirements.txt
```

## Setup

1. Copy `.env.example` to `.env`.
2. **Spotify:** create an app at <https://developer.spotify.com/dashboard>, add
   `http://localhost:8888/callback` as a redirect URI, and put the client ID and
   secret in `.env`. The first run opens a browser to authorize; the token is
   cached in `.spotify_token_cache`.
3. **YouTube Music:** run `ytmusicapi oauth` to create `oauth.json`, and point
   `YTMUSIC_AUTH_FILE` at it.

`.env`, token caches and auth files are git-ignored.

## Usage

```python
from music_api import SpotifyClient, YTMusicClient

sp = SpotifyClient()
for pl in sp.list_playlists():
    print(pl["name"], pl["tracks"]["total"])
liked = sp.get_saved_tracks(limit=100)

yt = YTMusicClient()
playlists = yt.list_playlists(limit=100)
pid = yt.create_playlist("New mix")
hit = yt.search("Bohemian Rhapsody", limit=1)[0]
yt.add_tracks(pid, [hit["videoId"]])
```

Run the demo with `python examples/demo.py` (or `--spotify` / `--ytmusic`).

## API

**`SpotifyClient`** — `list_playlists`, `get_playlist`, `get_playlist_tracks`,
`get_saved_tracks`, `iter_saved_tracks`, `get_saved_albums`, `search`.
Pagination is handled automatically.

**`YTMusicClient`** — `list_playlists`, `get_playlist`, `get_playlist_tracks`,
`create_playlist`, `delete_playlist`, `add_tracks`, `remove_tracks`,
`get_liked_songs`, `get_library_songs`, `get_library_albums`, `search`.

Note: there is no official YouTube Music API; `ytmusicapi` is a well-maintained
unofficial client.
