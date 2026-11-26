"""A fake live stream (real-time HLS, like Twitch/YouTube serve) and a fake
Twitch chat (IRC), so live mode can be tested without the real platforms."""
import functools
import http.server
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FakeLiveStream:
    """ffmpeg -re pushes `src` (looped) as live HLS; served on 127.0.0.1:port."""

    def __init__(self, src: Path, root: Path, host: str = "127.0.0.1"):
        self.src, self.root = src, root
        self.root.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(_QuietHandler, directory=str(root))
        self.httpd = socketserver.ThreadingTCPServer((host, 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.proc = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/live.m3u8"

    def start(self):
        for p in self.root.glob("*"):
            p.unlink()
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", str(self.src),
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-sc_threshold", "0",
             "-c:a", "aac", "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
             "-hls_flags", "delete_segments+omit_endlist", str(self.root / "live.m3u8")],
            stdin=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline and not (self.root / "live.m3u8").exists():
            time.sleep(0.2)

    def drop(self):
        """Abrupt outage: the encoder dies, the playlist stops updating."""
        if self.proc:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait()
            self.proc = None

    def end(self):
        """Clean end of broadcast: playlist gets #EXT-X-ENDLIST, then disappears."""
        self.drop()
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def close(self):
        self.drop()
        self.httpd.shutdown()


class FakeTwitchChat:
    """Minimal Twitch IRC server: accepts the anonymous login and lets tests
    push PRIVMSG lines to every connected client."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.clients: list[socket.socket] = []
        self.joined = threading.Event()
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        while True:
            try:
                data = conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                if line.startswith(b"JOIN"):
                    with self.lock:
                        self.clients.append(conn)
                    conn.sendall(b":tmi.twitch.tv 001 justinfan :Welcome\r\n")
                    self.joined.set()

    def say(self, text: str, user: str = "viewer"):
        line = f"@badges=;color= :{user}!{user}@{user}.tmi.twitch.tv PRIVMSG #canal :{text}\r\n".encode()
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(line)
                except OSError:
                    self.clients.remove(c)

    def kick_all(self):
        """Simulates the chat connection dropping."""
        with self.lock:
            for c in self.clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self.clients.clear()
        self.joined.clear()

    def close(self):
        self.kick_all()
        self.sock.close()
