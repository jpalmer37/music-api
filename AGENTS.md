# music-api

Personal Python/CLI tools for Spotify and YouTube Music libraries. Runtime
versions and the console entry point are declared in `pyproject.toml`;
`requirements.txt` installs that package. Python 3.10+, macOS/Linux.

- `config.py` reads a selected .env file without changing process environment;
  environment variables override it. No credential loading at import time.
- Clients retain native response shapes. Spotify playlists use `item`, saved
  songs use `track`. YouTube playlist removal/movement needs `setVideoId`.
- `transfer.py` owns normalization, matching, complete source collection, saved
  plans, progress, locking, and verification. `cli.py` owns interactive review.
- Preserve ordered occurrences. Never deduplicate source tracks or automatically
  skip uncertain entries. Copies are new private playlists.
- Applied plans are frozen and fingerprinted. Write intent is persisted before
  remote changes. Never replay an ambiguous write or reduce confirmed progress
  because of a stale read. Recovery binds an explicitly supplied destination.
- No transport retries on writes. Read retries are bounded; authentication and
  quota exhaustion are terminal. The pinned Spotipy addition body needs the
  wrapper's documented transport correction.
- Run `python -m unittest discover -s tests -v`. Offline tests use fake services
  and mocked real SDK transports. No live credentials belong in tests/fixtures.
- Changes to interfaces or transfer behavior must update README and examples.
  Live service verification is separate from offline test results.
