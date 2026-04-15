"""Enumeration + assignment tests. No real camera needed — a fake sysfs
tree is built on tmp_path mirroring the real layout.
"""

from pathlib import Path

import pytest

from ddb.camera.config import CameraAssignments, load_assignments, save_assignments
from ddb.camera.enumeration import discover_cameras


def _make_fake_video_node(root: Path, name: str, label: str, bus_path: str) -> None:
    node = root / name
    node.mkdir(parents=True)
    (node / "name").write_text(label + "\n")

    # Mirror the real shape: device symlink points at something containing
    # the bus path segment ("1-6.1:1.0").
    device_target = root.parent / "devices" / f"{bus_path}:1.0"
    device_target.mkdir(parents=True, exist_ok=True)
    (node / "device").symlink_to(device_target)


@pytest.fixture()
def fake_sysfs(tmp_path: Path) -> Path:
    sysfs = tmp_path / "sys" / "class" / "video4linux"
    # Two physical cameras, each with a capture node + a metadata node.
    _make_fake_video_node(sysfs, "video0", "USB2.0 camera", "1-6.1")
    _make_fake_video_node(sysfs, "video1", "USB2.0 camera", "1-6.1")
    _make_fake_video_node(sysfs, "video2", "USB 2.0 Camera", "1-6.2")
    _make_fake_video_node(sysfs, "video3", "USB 2.0 Camera", "1-6.2")
    return sysfs


def test_discovery_collapses_metadata_siblings(fake_sysfs: Path) -> None:
    cams = discover_cameras(fake_sysfs)
    assert len(cams) == 2

    first, second = cams
    assert first.bus_path == "1-6.1"
    assert first.device_path == "/dev/video0"  # lowest-numbered == capture
    assert first.name == "USB2.0 camera"

    assert second.bus_path == "1-6.2"
    assert second.device_path == "/dev/video2"


def test_discovery_returns_empty_when_no_sysfs(tmp_path: Path) -> None:
    assert discover_cameras(tmp_path / "does_not_exist") == []


def test_assignments_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cameras.json"
    a = CameraAssignments()
    a.set("front", "1-6.1")
    a.set("back", "1-6.2")
    save_assignments(a, path)

    loaded = load_assignments(path)
    assert loaded.bus_path_for("front") == "1-6.1"
    assert loaded.bus_path_for("back") == "1-6.2"


def test_reassigning_same_camera_clears_previous_role(tmp_path: Path) -> None:
    a = CameraAssignments()
    a.set("front", "1-6.1")
    # User realises they got it wrong and re-labels the same camera "back":
    a.set("back", "1-6.1")
    assert a.bus_path_for("front") is None
    assert a.bus_path_for("back") == "1-6.1"


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_assignments(tmp_path / "nope.json").roles == {}
