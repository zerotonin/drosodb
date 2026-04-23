"""File backend — writes raster to disk. Useful for tests and offline prep.

The printer never sees the bytes, so it returns no status blocks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ddb.printing.status import StatusBlock


class FileBackend:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    @property
    def description(self) -> str:
        return f"file:{self.out_dir}"

    def send(self, raster: bytes, *, timeout: float = 30.0) -> list[StatusBlock]:  # noqa: ARG002
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = self.out_dir / f"job_{stamp}.bin"
        path.write_bytes(raster)
        return []
