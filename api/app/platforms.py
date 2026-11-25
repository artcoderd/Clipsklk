from urllib.parse import urlparse

# host suffix -> platform id
_HOSTS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "twitch.tv": "twitch",
    "kick.com": "kick",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
}


def detect_platform(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    for suffix, platform in _HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return platform
    return None


def canonical_source(url: str) -> str:
    """Stable key so the same video is stored once even if the link differs
    (youtu.be vs youtube.com, tracking params, timestamps...)."""
    from urllib.parse import parse_qs

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        return f"youtube:{parsed.path.strip('/').split('/')[0]}"
    if host.endswith("youtube.com"):
        vid = parse_qs(parsed.query).get("v", [None])[0]
        if vid:
            return f"youtube:{vid}"
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return f"youtube:{parts[1]}"
    return f"{host}{parsed.path.rstrip('/')}"
