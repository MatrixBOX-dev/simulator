"""HTTP server for the web renderer: serves the page itself and, on the same
port, a WebSocket that relays the app's wire messages to every open tab.
The browser decodes and draws them, so this side never looks at pixels.
Controls flow the other way: whatever a tab sends is handed on to the app.
"""

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from matrixbox_simulator.device.wsserver import (
    OPCODE_TEXT,
    Broadcaster,
    accept_key,
    read_message,
)
from matrixbox_simulator.term import wire

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
WEBSOCKET_PATH = "/ws"


class WebServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8081) -> None:
        self.broadcaster = Broadcaster()
        # Set while there's an app to pass controls on to; tabs sending
        # controls with nothing connected are simply ignored.
        self.on_control: Callable[[str], None] | None = None
        self._stopping = threading.Event()
        self._http = ThreadingHTTPServer((host, port), self._handler_class())
        self._http.daemon_threads = True

    @property
    def port(self) -> int:
        return self._http.server_address[1]

    def start(self) -> None:
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        self._http.shutdown()
        self._http.server_close()

    def publish(self, message: bytes) -> None:
        """Forwards one still-encoded wire message to every open page."""
        decoded = wire.decode(message)
        kind = "frame" if isinstance(decoded, wire.Frame) else "stats"
        self.broadcaster.broadcast(message, kind=kind)

    def publish_status(self, *, connected: bool, message: str) -> None:
        if not connected:
            # Stale numbers from an app that's gone would read as if it
            # were still running.
            self.broadcaster.forget("stats")

        payload = json.dumps({"connected": connected, "message": message})
        self.broadcaster.broadcast(payload.encode(), kind="status", text=True)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == WEBSOCKET_PATH:
                    self._upgrade()
                    return

                static = STATIC_FILES.get(path)
                if static is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                name, content_type = static
                body = (STATIC_DIR / name).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                # Always fresh, so editing the page while it's open only
                # needs a reload, not a cache bust.
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _upgrade(self) -> None:
                key = self.headers.get("Sec-WebSocket-Key")
                if key is None:
                    self.send_error(HTTPStatus.BAD_REQUEST, "expected a WebSocket")
                    return

                if not self._is_same_origin():
                    self.send_error(HTTPStatus.FORBIDDEN, "foreign origin")
                    return

                self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept_key(key))
                self.end_headers()
                self.wfile.flush()

                conn = self.connection
                server.broadcaster.add_client(conn)
                try:
                    # Also keeps this handler, and so the socket, alive for
                    # as long as the tab stays open.
                    while not server._stopping.is_set():
                        message = read_message(conn)
                        if message is None:
                            return

                        opcode, payload = message
                        if opcode == OPCODE_TEXT and server.on_control is not None:
                            server.on_control(payload.decode("utf-8", "replace"))
                finally:
                    server.broadcaster.remove_client(conn)
                    self.close_connection = True

            def _is_same_origin(self) -> bool:
                # Any website can open a WebSocket here from the viewer's
                # browser, and these sockets can drive the app, so only the
                # page this server itself serves gets one.
                origin = self.headers.get("Origin")
                if origin is None:
                    return True

                return origin.split("://", 1)[-1] == self.headers.get("Host")

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler
