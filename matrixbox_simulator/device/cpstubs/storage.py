"""Stand-in for CircuitPython's `storage`. The simulated filesystem has no
USB host competing for it, so it starts writable from code's point of view
and USB drive toggling is a no-op.

https://docs.circuitpython.org/en/latest/shared-bindings/storage/
"""


class VfsFat:
    def __init__(self, readonly: bool = False) -> None:
        self.readonly = readonly
        self.label = "CIRCUITPY"


_root_mount = VfsFat()


def getmount(mount_path: str) -> VfsFat:
    return _root_mount


def remount(
    mount_path: str,
    readonly: bool = False,
    *,
    disable_concurrent_write_protection: bool = False,
) -> None:
    getmount(mount_path).readonly = readonly


def disable_usb_drive() -> None:
    pass


def enable_usb_drive() -> None:
    pass
