from matrixbox_simulator.device.cpstubs import storage


def test_root_mount_starts_writable() -> None:
    assert storage.getmount("/").readonly is False


def test_remount_updates_readonly_flag() -> None:
    storage.remount("/", True)
    assert storage.getmount("/").readonly is True

    storage.remount("/", False)
    assert storage.getmount("/").readonly is False
