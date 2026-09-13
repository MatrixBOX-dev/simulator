"""Stand-in for CircuitPython's `wifi`. The sim's host machine is assumed to
already have real network access, so `radio` reports itself as permanently
connected rather than emulating association/DHCP.

The class is deliberately named `Radio` (not e.g. `SimRadio`). The vendored
`adafruit_connection_manager` dispatches on `radio.__class__.__name__` and
only special-cases the real CircuitPython name.
"""

import os

_LOCAL_IPV4_ADDRESS = (
    f"127.0.0.1:{os.environ.get('MATRIXBOX_SIMULATOR_HTTP_PORT', '8080')}"
)


class _ApInfo:
    def __init__(self, rssi: int) -> None:
        self.rssi = rssi


class Radio:
    def __init__(self) -> None:
        self.connected: bool = True
        self.ap_active: bool = False
        self.mac_address: bytes = bytes([0x02, 0x00, 0x00, 0x45, 0x53, 0x50])
        self.tx_power: float = 0.0
        self.ipv4_address: str | None = _LOCAL_IPV4_ADDRESS
        self.ipv4_address_ap: str | None = _LOCAL_IPV4_ADDRESS

        # The sim has no real association to measure RSSI from. -50 dBm
        # lands in matrixbox's own "4 of 5" signal-bar bracket (see
        # web_interface._sig_bars) instead of the "no signal" 0 bars that
        # an absent ap_info used to fall back to.
        self.ap_info: _ApInfo | None = _ApInfo(rssi=-50)

    def connect(
        self, ssid: str, password: str, *, channel: int = 0, timeout: float = 15
    ) -> None:
        pass

    def start_ap(self, ssid: str, *args: object, **kwargs: object) -> None:
        self.ap_active = True

    def stop_ap(self) -> None:
        self.ap_active = False

    def stop_dhcp(self) -> None:
        pass

    def set_ipv4_address(self, **kwargs: object) -> None:
        pass


radio = Radio()


def set_connected(value: bool) -> None:
    # Flipped live from the sim's own controls (not app code) to test how
    # an app behaves with no internet. ap_info drops out along with it,
    # matching a real radio that's no longer associated with any AP.
    radio.connected = value
    radio.ap_info = _ApInfo(rssi=-50) if value else None
