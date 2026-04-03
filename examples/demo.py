#!/usr/bin/env python3
"""Demo script showing basic usage of the Music API Toolkit.

Before running, ensure you have:
  1. Copied .env.example to .env and filled in Spotify credentials
  2. Run `ytmusicapi oauth` to generate the YouTube Music auth file
  3. Installed dependencies: pip install -r requirements.txt
"""

from music_api import SpotifyClient, YTMusicClient


def spotify_demo():
    """Demonstrate Spotify client usage."""
    print("=" * 60)
    print("SPOTIFY DEMO")
    print("=" * 60)

    sp = SpotifyClient()

    # List all playlists
    playlists = sp.list_playlists()
    print(f"\nFound {len(playlists)} playlists:")
    for pl in playlists[:10]:
        track_count = pl["tracks"]["total"]
        print(f"  - {pl['name']} ({track_count} tracks) [id: {pl['id']}]")

    if playlists:
        print(f"  ... showing first 10 of {len(playlists)}")

    # Get tracks from the first playlist
    if playlists:
        first_pl = playlists[0]
        tracks = sp.get_playlist_tracks(first_pl["id"], limit=5)
        print(f"\nFirst 5 tracks in '{first_pl['name']}':")
        for item in tracks:
            track = item["track"]
            artists = ", ".join(a["name"] for a in track["artists"])
            print(f"  - {track['name']} by {artists}")

    # Get liked songs
    saved = sp.get_saved_tracks(limit=5)
    print(f"\nFirst 5 liked songs:")
    for item in saved:
        track = item["track"]
        artists = ", ".join(a["name"] for a in track["artists"])
        print(f"  - {track['name']} by {artists}")

    # Search
    results = sp.search("Bohemian Rhapsody", limit=3)
    print("\nSearch results for 'Bohemian Rhapsody':")
    for item in results["tracks"]["items"]:
        artists = ", ".join(a["name"] for a in item["artists"])
        print(f"  - {item['name']} by {artists}")


def ytmusic_demo():
    """Demonstrate YouTube Music client usage."""
    print("\n" + "=" * 60)
    print("YOUTUBE MUSIC DEMO")
    print("=" * 60)

    yt = YTMusicClient()

    # List all playlists
    playlists = yt.list_playlists(limit=25)
    print(f"\nFound {len(playlists)} playlists:")
    for pl in playlists[:10]:
        count = pl.get("count", "?")
        print(f"  - {pl['title']} ({count} tracks) [id: {pl['playlistId']}]")

    if len(playlists) > 10:
        print(f"  ... showing first 10 of {len(playlists)}")

    # Get tracks from the first playlist
    if playlists:
        first_pl = playlists[0]
        tracks = yt.get_playlist_tracks(first_pl["playlistId"], limit=5)
        print(f"\nFirst 5 tracks in '{first_pl['title']}':")
        for t in tracks[:5]:
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            print(f"  - {t.get('title', 'Unknown')} by {artists}")

    # Get liked songs
    liked = yt.get_liked_songs(limit=5)
    print(f"\nFirst 5 liked songs:")
    for t in liked[:5]:
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        print(f"  - {t.get('title', 'Unknown')} by {artists}")

    # Search
    results = yt.search("Bohemian Rhapsody", limit=3)
    print("\nSearch results for 'Bohemian Rhapsody':")
    for item in results[:3]:
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        print(f"  - {item.get('title', 'Unknown')} by {artists}")


if __name__ == "__main__":
    import sys

    if "--spotify" in sys.argv or len(sys.argv) == 1:
        try:
            spotify_demo()
        except Exception as e:
            print(f"\nSpotify demo failed: {e}")

    if "--ytmusic" in sys.argv or len(sys.argv) == 1:
        try:
            ytmusic_demo()
        except Exception as e:
            print(f"\nYouTube Music demo failed: {e}")
