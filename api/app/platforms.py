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
