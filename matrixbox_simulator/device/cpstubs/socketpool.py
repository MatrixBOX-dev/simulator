"""Stand-in for CircuitPython's `socketpool`. Backed by real desktop
sockets, since the sim assumes genuine network access, so requests
actually go out rather than being re-simulated.
"""

import errno
import os
import socket as _socket

# The device binds its web server to port 80; unprivileged desktop processes
# usually can't. Remap transparently so matrixbox's own code needs no change.
_HTTP_PORT_REMAP = int(os.environ.get("MATRIXBOX_SIMULATOR_HTTP_PORT", "8080"))


def _require_connected(radio: object) -> None:
    # Real matrixbox code (e.g. fetch_data.fetch) very often opens a socket
    # straight away without checking wifi.radio.connected first, relying on
    # the connection attempt itself to fail when there's no network. The
    # host machine always has real network access, so that failure has to
    # be injected here instead, or toggling `connected` off from the sim's
    # own controls would be invisible to that code.
    if not getattr(radio, "connected", True):
        raise OSError(errno.ENETUNREACH, "Network is unreachable")


class _Socket:
    """Wraps a real socket, only overriding bind() for the port 80 remap
    and connect() to fail while the radio is simulated as disconnected.
    Everything else (send, recv_into, settimeout, ...) passes straight
    through to the real socket object."""

    def __init__(self, sock: _socket.socket, radio: object) -> None:
        self._sock = sock
        self._radio = radio

    def bind(self, address: tuple[str, int]) -> None:
        host, port = address
        if port == 80:
            port = _HTTP_PORT_REMAP

        return self._sock.bind((host, port))

    def connect(self, address: tuple[str, int]) -> None:
        _require_connected(self._radio)
        return self._sock.connect(address)

    def __getattr__(self, name: str) -> object:
        return getattr(self._sock, name)

    def __enter__(self) -> "_Socket":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._sock.close()


class SocketPool:
    SOCK_STREAM = _socket.SOCK_STREAM
    SOCK_DGRAM = _socket.SOCK_DGRAM
    AF_INET = _socket.AF_INET
    SOL_SOCKET = _socket.SOL_SOCKET
    SO_REUSEADDR = _socket.SO_REUSEADDR

    def __init__(self, radio: object) -> None:
        self._radio = radio

    def socket(
        self, family: int = _socket.AF_INET, type: int = _socket.SOCK_STREAM
    ) -> _Socket:
        return _Socket(_socket.socket(family, type), self._radio)

    def getaddrinfo(
        self,
        host: str,
        port: int,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple]:
        _require_connected(self._radio)
        return _socket.getaddrinfo(host, port, family or _socket.AF_INET, type)
