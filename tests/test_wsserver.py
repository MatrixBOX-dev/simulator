import queue
import socket

import pytest
import websocket

from matrixbox_simulator.device.wsserver import FrameServer, is_local_origin


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))

        return probe.getsockname()[1]


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        (None, True),
        ("http://127.0.0.1:8081", True),
        ("http://localhost:3000", True),
        ("http://[::1]:8081", True),
        ("https://example.com", False),
        ("http://127.0.0.1.example.com", False),
    ],
)
def test_only_local_origins_are_allowed(origin: str | None, allowed: bool) -> None:
    assert is_local_origin(origin) is allowed


def test_text_messages_from_clients_reach_the_command_handler() -> None:
    commands: queue.Queue[str] = queue.Queue()
    port = free_port()
    FrameServer("127.0.0.1", port, on_command=commands.put)

    client = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=2)
    try:
        client.send('{"control": "short_press"}')
        client.send("x" * 300)
        assert commands.get(timeout=2) == '{"control": "short_press"}'
        assert commands.get(timeout=2) == "x" * 300
    finally:
        client.close()


def test_foreign_origins_are_refused() -> None:
    port = free_port()
    FrameServer("127.0.0.1", port)

    with pytest.raises(websocket.WebSocketException):
        websocket.create_connection(
            f"ws://127.0.0.1:{port}", timeout=2, origin="https://example.com"
        )
