"""Parse service IDs, URIs, and links without accepting arbitrary URLs."""

import re
from urllib.parse import parse_qs, urlparse


def identifier(value: str, service: str, kind: str = "playlist") -> str:
    value = value.strip()
    if service not in {"spotify", "ytmusic"} or kind not in {"playlist", "track"}:
        raise ValueError("Unknown service or item type")
    if value.startswith("spotify:"):
        parts = value.split(":")
        if service != "spotify" or len(parts) != 3 or parts[1] != kind:
            raise ValueError(f"Expected a {service} {kind} URI")
        value = parts[2]
    elif "://" in value:
        link = urlparse(value)
        parts = link.path.strip("/").split("/")
        if service == "spotify" and link.hostname == "open.spotify.com":
            if parts[0].startswith("intl-"):
                parts = parts[1:]
            if len(parts) != 2 or parts[0] != kind:
                raise ValueError(f"Expected a Spotify {kind} link")
            value = parts[1]
        elif service == "ytmusic" and link.hostname in {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}:
            query = parse_qs(link.query)
            value = query.get("list" if kind == "playlist" else "v", [""])[0]
            if kind == "track" and link.hostname == "youtu.be":
                value = link.path.strip("/")
        else:
            raise ValueError(f"Expected a {service} link")
    pattern = r"[A-Za-z0-9]+" if service == "spotify" else r"[A-Za-z0-9_-]+"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {service} {kind} ID")
    return value


def item_link(service: str, item_id: str, kind: str = "track") -> str:
    if service == "spotify":
        return f"https://open.spotify.com/{kind}/{item_id}"
    key = "list" if kind == "playlist" else "v"
    page = "playlist" if kind == "playlist" else "watch"
    return f"https://music.youtube.com/{page}?{key}={item_id}"
