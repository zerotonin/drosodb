"""Persist which physical camera (by USB bus path) plays which role.

Stored in `~/.config/ddb/cameras.json` so it survives across sessions and
is independent of the project directory. Format:

    {"front": "1-6.1", "back": "1-6.2"}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

Role = str  # "front" | "back" — kept open for future roles (e.g. "scanner")

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("DDB_CAMERA_CONFIG", Path.home() / ".config" / "ddb" / "cameras.json")
)


@dataclass
class CameraAssignments:
    roles: dict[Role, str] = field(default_factory=dict)  # role -> bus_path

    def set(self, role: Role, bus_path: str) -> None:
        # A camera can only fill one role; clear any previous role using this
        # bus_path so swaps don't leave stale mappings.
        self.roles = {r: bp for r, bp in self.roles.items() if bp != bus_path}
        self.roles[role] = bus_path

    def bus_path_for(self, role: Role) -> str | None:
        return self.roles.get(role)


def load_assignments(path: Path = DEFAULT_CONFIG_PATH) -> CameraAssignments:
    if not path.exists():
        return CameraAssignments()
    data = json.loads(path.read_text())
    return CameraAssignments(roles=dict(data))


def save_assignments(assignments: CameraAssignments, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(assignments.roles, indent=2, sort_keys=True) + "\n")
