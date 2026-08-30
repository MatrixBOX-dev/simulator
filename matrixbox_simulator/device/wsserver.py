"""A minimal, dependency-free WebSocket server (RFC 6455) for pushing binary
frames to renderer clients. Only implements the server to client direction
needed here: handshake plus unmasked binary frame broadcast, with no
incoming message parsing.
"""

import base64
import hashlib
import socket
import struct
import threading

_HANDSHAKE_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FrameServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9191) -> None:
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

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)

        threading.Thread(target=self._accept_loop, daemon=True).start()

    def broadcast(self, payload: bytes, *, kind: str) -> None:
        frame = _encode_binary_frame(payload)
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

    def _accept_loop(self) -> None:
        while True:
            conn, _addr = self._sock.accept()
            threading.Thread(target=self._handshake, args=(conn,), daemon=True).start()

    def _handshake(self, conn: socket.socket) -> None:
        try:
            request = conn.recv(4096).decode("utf-8", "replace")
            key = _extract_header(request, "sec-websocket-key")
            if key is None:
                conn.close()
                return

            accept = base64.b64encode(
                hashlib.sha1((key + _HANDSHAKE_GUID).encode()).digest()
            ).decode()
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            conn.sendall(response.encode())
            conn.settimeout(1.0)

            with self._lock:
                for cached in self._last_by_kind.values():
                    conn.sendall(cached)

                self._clients.append(conn)
        except OSError:
            conn.close()


def _extract_header(request: str, name: str) -> str | None:
    for line in request.split("\r\n"):
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        if key.strip().lower() == name:
            return value.strip()

    return None


def _encode_binary_frame(payload: bytes) -> bytes:
    header = bytearray([0x82])  # FIN + opcode 0x2 (binary)
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
