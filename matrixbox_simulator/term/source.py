"""Pulls frames (and stats sidecars) from the simulator's WebSocket frame
server.
"""

import sys

import websocket

from matrixbox_simulator.term import wire

# Sentinels for the two non-message outcomes next_frame() can return,
# alongside a real wire.Frame or wire.Stats.


class Idle:
    pass


class Disconnected:
    pass


IDLE = Idle()
DISCONNECTED = Disconnected()

# Bounds how long next_frame() can block, so a caller polling a shutdown
# flag or watching for a dead connection isn't stuck in a single recv()
# indefinitely.
_RECV_TIMEOUT_SECONDS = 0.3


class WsSource:
    def __init__(self, url: str) -> None:
        self._ws = websocket.create_connection(url, timeout=_RECV_TIMEOUT_SECONDS)

    def next_frame(self) -> "Idle | Disconnected | wire.Frame | wire.Stats":
        try:
            opcode, data = self._ws.recv_data()
        except websocket.WebSocketTimeoutException:
            return IDLE
        except Exception as err:
            print(f"websocket error: {err}", file=sys.stderr)
            return DISCONNECTED

        if opcode == websocket.ABNF.OPCODE_CLOSE:
            return DISCONNECTED
        if opcode != websocket.ABNF.OPCODE_BINARY:
            return IDLE

        try:
            return wire.decode(data)
        except wire.DecodeError as err:
            print(f"dropping malformed message: {err}", file=sys.stderr)
            return IDLE

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass
