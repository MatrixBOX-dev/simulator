import json
import queue
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
import websocket

from matrixbox_simulator.term import wire
from matrixbox_simulator.web.server import WebServer


@pytest.fixture
def server() -> Iterator[WebServer]:
    web_server = WebServer("127.0.0.1", 0)
    web_server.start()
    yield web_server
    web_server.stop()


def test_serves_the_page(server: WebServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/") as response:
        body = response.read().decode()

    assert response.headers["Content-Type"].startswith("text/html")
    assert "app.js" in body


def test_unknown_paths_are_not_found(server: WebServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/../pyproject.toml")

    assert error.value.code == 404


def test_new_page_is_replayed_the_latest_frame_and_status(server: WebServer) -> None:
    frame = wire.Frame(width=2, height=1, pixels=bytes(6))
    server.publish(wire.encode_frame(frame))
    server.publish_status(connected=True, message="connected")

    client = websocket.create_connection(f"ws://127.0.0.1:{server.port}/ws", timeout=2)
    try:
        received = [client.recv(), client.recv()]
    finally:
        client.close()

    binary = next(message for message in received if isinstance(message, bytes))
    text = next(message for message in received if isinstance(message, str))
    assert wire.decode(binary) == frame
    assert json.loads(text) == {"connected": True, "message": "connected"}


def test_disconnect_drops_stale_stats(server: WebServer) -> None:
    stats = wire.Stats(app="clock", fps=1.0, cpu_percent=None, rss_kb=None)
    server.publish(wire.encode_stats(stats))
    server.publish_status(connected=False, message="waiting")

    client = websocket.create_connection(f"ws://127.0.0.1:{server.port}/ws", timeout=2)
    try:
        status = client.recv()
        client.settimeout(0.3)
        with pytest.raises(websocket.WebSocketTimeoutException):
            client.recv()
    finally:
        client.close()

    assert json.loads(status)["connected"] is False


def test_controls_from_a_page_are_forwarded(server: WebServer) -> None:
    controls: queue.Queue[str] = queue.Queue()
    server.on_control = controls.put

    client = websocket.create_connection(f"ws://127.0.0.1:{server.port}/ws", timeout=2)
    try:
        client.send('{"control": "cycle_size"}')
        assert controls.get(timeout=2) == '{"control": "cycle_size"}'
    finally:
        client.close()


def test_pages_from_other_origins_are_refused(server: WebServer) -> None:
    with pytest.raises(websocket.WebSocketException):
        websocket.create_connection(
            f"ws://127.0.0.1:{server.port}/ws", timeout=2, origin="https://example.com"
        )
