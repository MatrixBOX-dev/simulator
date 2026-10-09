"""A minimal, dependency-free WebSocket server (RFC 6455) for pushing binary
frames to renderer clients, and reading back the small, unfragmented text
messages renderers send as controls (button presses and the like).
"""

import base64
import hashlib
import socket
import struct
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

_HANDSHAKE_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Broadcaster:
    """Tracks connected, already-handshaken clients and fans messages out to
    them. Shared by the device's own frame server and the web renderer's
    relay, which accept connections differently but broadcast the same way.
    """

    def __init__(self) -> None:
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        # Replayed to every newly-connecting client so it never has to wait
        # for the app's next refresh() to see anything, including a client
        # that connects (or reconnects) before the app has drawn at all.
        # Keyed by message kind ("frame", "stats") so a stats broadcast
        # (which happens right after every frame) doesn't clobber the last
        # actual frame. A client that connects between the two would
        # otherwise only ever get replayed the stats message.
        self._last_by_kind: dict[str, bytes] = {}

    def broadcast(self, payload: bytes, *, kind: str, text: bool = False) -> None:
        frame = encode_frame(payload, text=text)
        with self._lock:
            self._last_by_kind[kind] = frame
            dead: list[socket.socket] = []
            for conn in self._clients:
                try:
                    conn.sendall(frame)
                except OSError:
                    dead.append(conn)

            for conn in dead:
                self._clients.remove(conn)
                conn.close()

    def forget(self, kind: str) -> None:
        with self._lock:
            self._last_by_kind.pop(kind, None)

    def add_client(self, conn: socket.socket) -> None:
        conn.settimeout(1.0)
        with self._lock:
            for cached in self._last_by_kind.values():
                conn.sendall(cached)

            self._clients.append(conn)

    def remove_client(self, conn: socket.socket) -> None:
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)


class FrameServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9191,
        on_command: Callable[[str], None] | None = None,
    ) -> None:
        self._broadcaster = Broadcaster()
        self._on_command = on_command

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)

        threading.Thread(target=self._accept_loop, daemon=True).start()

    def broadcast(self, payload: bytes, *, kind: str) -> None:
        self._broadcaster.broadcast(payload, kind=kind)

    def _accept_loop(self) -> None:
        while True:
            conn, _addr = self._sock.accept()
            threading.Thread(target=self._handshake, args=(conn,), daemon=True).start()

    def _handshake(self, conn: socket.socket) -> None:
        try:
            request = conn.recv(4096).decode("utf-8", "replace")
            key = _extract_header(request, "sec-websocket-key")
            if key is None or not is_local_origin(_extract_header(request, "origin")):
                conn.close()
                return

            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
            )
            conn.sendall(response.encode())
            self._broadcaster.add_client(conn)
        except OSError:
            conn.close()
            return

        try:
            while (message := read_message(conn)) is not None:
                opcode, payload = message
                if opcode == OPCODE_TEXT and self._on_command is not None:
                    self._on_command(payload.decode("utf-8", "replace"))
        finally:
            self._broadcaster.remove_client(conn)
            conn.close()


def is_local_origin(origin: str | None) -> bool:
    # Browsers let any website open a WebSocket to localhost, so a page
    # from elsewhere must not be able to drive the app. Non-browser
    # clients send no Origin at all.
    if origin is None:
        return True

    return urlsplit(origin).hostname in _LOCAL_HOSTS


def read_message(conn: socket.socket) -> tuple[int, bytes] | None:
    """Blocks until one whole client message arrives, returning its opcode
    and unmasked payload, or None once the client closes or goes away."""
    try:
        first, second = _recv_exact(conn, 2)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", _recv_exact(conn, 2))
        elif length == 127:
            (length,) = struct.unpack(">Q", _recv_exact(conn, 8))

        mask = _recv_exact(conn, 4) if second & 0x80 else bytes(4)
        payload = bytes(
            byte ^ mask[index % 4]
            for index, byte in enumerate(_recv_exact(conn, length))
        )
    except (ConnectionError, OSError):
        return None

    opcode = first & 0x0F
    if opcode == OPCODE_CLOSE:
        return None

    return opcode, payload


def _recv_exact(conn: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        try:
            chunk = conn.recv(count - len(data))
        except TimeoutError:
            continue

        if not chunk:
            raise ConnectionError("client went away")

        data += chunk

    return bytes(data)


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + _HANDSHAKE_GUID).encode()).digest()

    return base64.b64encode(digest).decode()


def _extract_header(request: str, name: str) -> str | None:
    for line in request.split("\r\n"):
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        if key.strip().lower() == name:
            return value.strip()

    return None


def encode_frame(payload: bytes, *, text: bool = False) -> bytes:
    # FIN bit set: every message goes out as a single, unfragmented frame.
    header = bytearray([0x80 | (OPCODE_TEXT if text else OPCODE_BINARY)])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)

    return bytes(header) + payload
