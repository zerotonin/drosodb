"""Discover V4L2 cameras from sysfs — no OpenCV dependency.

Why the bus path matters: `/dev/video*` numbering is assigned by the kernel
in enumeration order and can shuffle between reboots or on USB replug. The
USB bus path (e.g. `1-6.1`) is tied to the physical port and is stable, so
that's what we persist as the identity of a "front" or "back" camera.

Each UVC camera typically exposes two video nodes on the same bus path
(one for capture, one for metadata). We group by bus path and keep only
the lowest-numbered node, which is the capture endpoint on every UVC
device observed so far.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SYSFS_ROOT = Path("/sys/class/video4linux")

# Bus path looks like "1-6.1:1.0" in the sysfs device link; we want "1-6.1".
_BUS_PATH_RE = re.compile(r"(\d+-[\d.]+?)(?::|$)")


@dataclass(frozen=True)
class CameraInfo:
    """A single physical camera, keyed by its stable USB bus path."""

    bus_path: str  # e.g. "1-6.1" — stable across reboots
    name: str  # vendor-provided, e.g. "USB2.0 camera"
    device_path: str  # the capture node, e.g. "/dev/video0"
    sysfs_name: str  # e.g. "video0"


def _bus_path_from_device_link(device_link: Path) -> str | None:
    """Extract "1-6.1" from a sysfs device symlink target."""
    try:
        resolved = device_link.resolve()
    except OSError:
        return None
    for part in reversed(resolved.parts):
        m = _BUS_PATH_RE.match(part)
        if m:
            return m.group(1)
    return None


def _video_index(sysfs_name: str) -> int:
    """Numeric suffix of 'videoN'; used to pick the capture node per camera."""
    m = re.match(r"video(\d+)$", sysfs_name)
    return int(m.group(1)) if m else 10_000


def discover_cameras(sysfs_root: Path = SYSFS_ROOT) -> list[CameraInfo]:
    """Return one CameraInfo per physical camera, sorted by bus path.

    `sysfs_root` is injectable so tests can point at a fake tree.
    """
    if not sysfs_root.exists():
        return []

    # Collect every video node, then collapse duplicates by bus path.
    by_bus: dict[str, list[tuple[int, str, str]]] = {}
    for entry in sorted(sysfs_root.iterdir()):
        sysfs_name = entry.name
        name_file = entry / "name"
        device_link = entry / "device"
        if not name_file.exists() or not device_link.exists():
            continue
        bus_path = _bus_path_from_device_link(device_link)
        if bus_path is None:
            continue
        label = name_file.read_text(errors="replace").strip()
        by_bus.setdefault(bus_path, []).append((_video_index(sysfs_name), sysfs_name, label))

    cameras: list[CameraInfo] = []
    for bus_path, nodes in sorted(by_bus.items()):
        nodes.sort()  # lowest videoN first == capture endpoint
        idx, sysfs_name, name = nodes[0]
        cameras.append(
            CameraInfo(
                bus_path=bus_path,
                name=name,
                device_path=f"/dev/{sysfs_name}",
                sysfs_name=sysfs_name,
            )
        )
    return cameras
