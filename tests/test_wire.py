from matrixbox_simulator.term import wire


def test_frame_round_trips_through_encode_and_decode() -> None:
    frame = wire.Frame(width=2, height=1, pixels=bytes([1, 2, 3, 4, 5, 6]), tiles=2)

    assert wire.decode(wire.encode_frame(frame)) == frame


def test_stats_round_trip_keeps_optional_fields() -> None:
    stats = wire.Stats(app="clock", fps=30.0, cpu_percent=None, rss_kb=2048)

    assert wire.decode(wire.encode_stats(stats)) == stats
