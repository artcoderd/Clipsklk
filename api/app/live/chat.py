"""Live chat readers. Each one calls on_message(wall_time, text) and keeps
reconnecting (with back-off) until `stop` is set.

- Twitch: anonymous IRC (irc.chat.twitch.tv), the official read-only way.
- YouTube: chat-downloader (reads the public live chat).
- Kick:    its public Pusher websocket.
- TikTok:  not supported (its chat needs a third-party signing service);
           live clips there rely on audio only.
"""
import json
import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable
from urllib.parse import urlparse

from app.config import get_settings

log = logging.getLogger(__name__)

OnMessage = Callable[[float, str], None]
OnStatus = Callable[[str], None]

# Reactions that mean "something just happened" in Spanish/English chats.
HYPE_RE = re.compile(
    r"\b(?:ja(?:ja)+|je(?:je)+|xd+|lol|lmao|kekw|lul|omegalul|pog(?:gers|champ)?|"
    r"no+ ma+m+e+s|qu[eé]+ (?:fue|pas[oó])|w+o+w+|clip+|o+m+g+)\b|😂|🤣|😱|🔥|💀",
    re.I,
)


class ChatMeter:
    """Messages per wall-clock second (and how many are reactions)."""

    def __init__(self, keep_seconds: int = 4 * 3600):
        self.counts: dict[int, list[int]] = {}
        self.order: deque[int] = deque()
        self.keep = keep_seconds
        self.lock = threading.Lock()
        self.total = 0

    def add(self, wall: float, text: str) -> None:
        sec = int(wall)
        hype = 1 if HYPE_RE.search(text) else 0
        with self.lock:
            if sec not in self.counts:
                self.counts[sec] = [0, 0]
                self.order.append(sec)
                while self.order and self.order[0] < sec - self.keep:
                    self.counts.pop(self.order.popleft(), None)
            self.counts[sec][0] += 1
            self.counts[sec][1] += hype
            self.total += 1

    def get(self, sec: int) -> tuple[int, int]:
        with self.lock:
            return tuple(self.counts.get(sec, (0, 0)))


def _backoff_loop(name: str, stop: threading.Event, on_status: OnStatus, connect_once: Callable[[], None]):
    delay = 2.0
    while not stop.is_set():
        try:
            connect_once()
            delay = 2.0
        except Exception as e:   # network errors, platform changes...
            log.info("%s chat disconnected: %s", name, e)
        if stop.is_set():
            break
        on_status("reconnecting")
        stop.wait(delay)
        delay = min(60.0, delay * 2)


class TwitchChat:
    def __init__(self, channel: str, on_message: OnMessage, on_status: OnStatus, stop: threading.Event):
        self.channel = channel.lower().lstrip("#")
        self.on_message, self.on_status, self.stop = on_message, on_status, stop
        s = get_settings()
        self.host, self.port, self.tls = s.twitch_irc_host, s.twitch_irc_port, s.twitch_irc_tls

    def run(self) -> None:
        _backoff_loop("twitch", self.stop, self.on_status, self._once)

    def _once(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=10)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        sock.settimeout(1.0)
        try:
            nick = f"justinfan{int(time.time()) % 100000}"
            sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
            buf, last_rx = b"", time.time()
            while not self.stop.is_set():
                try:
                    data = sock.recv(8192)
                except TimeoutError:
                    if time.time() - last_rx > 330:   # Twitch pings every ~5 min
                        raise ConnectionError("chat silent for too long")
                    continue
                if not data:
                    raise ConnectionError("closed by server")
                last_rx = time.time()
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    self._handle(sock, line.decode("utf-8", "replace"))
        finally:
            sock.close()

    def _handle(self, sock, line: str) -> None:
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
        elif " 001 " in line or " JOIN " in line:
            self.on_status("connected")
        elif " PRIVMSG " in line:
            text = line.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1]
            self.on_message(time.time(), text)


class YouTubeChat:
    def __init__(self, url: str, on_message: OnMessage, on_status: OnStatus, stop: threading.Event):
        self.url, self.on_message, self.on_status, self.stop = url, on_message, on_status, stop

    def run(self) -> None:
        _backoff_loop("youtube", self.stop, self.on_status, self._once)

    def _once(self) -> None:
        from chat_downloader import ChatDownloader

        chat = ChatDownloader().get_chat(self.url, message_groups=["messages"])
        self.on_status("connected")
        for msg in chat:
            if self.stop.is_set():
                break
            self.on_message(time.time(), msg.get("message") or "")


class KickChat:
    PUSHER = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"

    def __init__(self, slug: str, on_message: OnMessage, on_status: OnStatus, stop: threading.Event):
        self.slug, self.on_message, self.on_status, self.stop = slug, on_message, on_status, stop

    def run(self) -> None:
        _backoff_loop("kick", self.stop, self.on_status, self._once)

    def _chatroom_id(self) -> int:
        import urllib.request
        req = urllib.request.Request(f"https://kick.com/api/v2/channels/{self.slug}",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return int(json.load(r)["chatroom"]["id"])

    def _once(self) -> None:
        from websockets.sync.client import connect

        room = self._chatroom_id()
        with connect(self.PUSHER, open_timeout=15) as ws:
            ws.send(json.dumps({"event": "pusher:subscribe",
                                "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
            self.on_status("connected")
            while not self.stop.is_set():
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("event") == "pusher:ping":
                    ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                elif msg.get("event") == "App\\Events\\ChatMessageEvent":
                    self.on_message(time.time(), json.loads(msg["data"]).get("content", ""))


def make_chat(platform: str | None, url: str, on_message: OnMessage, on_status: OnStatus,
              stop: threading.Event):
    """The chat reader for a live URL, or None when the platform has none we can read."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if platform == "twitch" and parts:
        return TwitchChat(parts[0], on_message, on_status, stop)
    if platform == "youtube":
        return YouTubeChat(url, on_message, on_status, stop)
    if platform == "kick" and parts:
        return KickChat(parts[0], on_message, on_status, stop)
    return None
