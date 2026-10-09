#!/usr/bin/env python3
"""Read-only examples for the configured personal libraries."""

import argparse
from pathlib import Path

from music_api import SpotifyClient, YTMusicClient, load_spotify_config, load_ytmusic_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--spotify", action="store_true")
    parser.add_argument("--ytmusic", action="store_true")
    args = parser.parse_args()
    both = not args.spotify and not args.ytmusic
    if args.spotify or both:
        sp = SpotifyClient(load_spotify_config(args.env_file))
        for playlist in sp.list_playlists(limit=10):
            print("Spotify:", playlist["name"], playlist["id"])
        for saved in sp.get_saved_tracks(limit=5):
            print(saved["track"]["name"])
    if args.ytmusic or both:
        yt = YTMusicClient(load_ytmusic_config(args.env_file))
        for playlist in yt.list_playlists(limit=10):
            print("YouTube Music:", playlist["title"], playlist["playlistId"])
        for track in yt.get_liked_songs(limit=5):
            print(track.get("title", "Unavailable"))


if __name__ == "__main__":
    main()
