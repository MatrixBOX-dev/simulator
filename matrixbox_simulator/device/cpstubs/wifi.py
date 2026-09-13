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

# --no-wifi boots the radio already disconnected, for testing an app's
# offline behavior from the very first frame rather than toggling it live
# with 'n' after boot. connect() is a no-op (see below), so nothing an
# app does on its own ever flips this back on — it stays offline for the
# whole run, same as the live toggle would leave it.
_START_CONNECTED = os.environ.get("MATRIXBOX_SIMULATOR_NO_WIFI", "0") != "1"


class _ApInfo:
    def __init__(self, rssi: int) -> None:
        self.rssi = rssi


class _Network:
    def __init__(self, ssid: str, channel: int) -> None:
        self.ssid = ssid
        self.channel = channel


# The sim has no way to scan real nearby networks portably, so the wifi
# setup page (matrixbox's own connect_to_wifi(), reached by disconnecting)
# is offered this fixed stand-in list instead of a real scan result.
_FAKE_SCAN_RESULTS = [_Network(ssid="matrixbox-simulator", channel=1)]


class Radio:
    def __init__(self) -> None:
        self.connected: bool = _START_CONNECTED
        self.ap_active: bool = False
        self.mac_address: bytes = bytes([0x02, 0x00, 0x00, 0x45, 0x53, 0x50])
        self.tx_power: float = 0.0
        self.ipv4_address: str | None = _LOCAL_IPV4_ADDRESS
        self.ipv4_address_ap: str | None = _LOCAL_IPV4_ADDRESS

        # The sim has no real association to measure RSSI from. -50 dBm
        # lands in matrixbox's own "4 of 5" signal-bar bracket (see
        # web_interface._sig_bars) instead of the "no signal" 0 bars that
        # an absent ap_info used to fall back to.
        self.ap_info: _ApInfo | None = _ApInfo(rssi=-50) if _START_CONNECTED else None

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

    def start_scanning_networks(
        self, *, start_channel: int = 1, stop_channel: int = 11
    ) -> list[_Network]:
        return _FAKE_SCAN_RESULTS

    def stop_scanning_networks(self) -> None:
        pass


radio = Radio()


def set_connected(value: bool) -> None:
    # Flipped live from the sim's own controls (not app code) to test how
    # an app behaves with no internet. ap_info drops out along with it,
    # matching a real radio that's no longer associated with any AP.
    radio.connected = value
    radio.ap_info = _ApInfo(rssi=-50) if value else None
