"""Progress dialog that downloads the FlyBase stocks catalog.

Runs `ddb.flybase.catalog.download_catalog` inside a QThread so the
GUI stays responsive, and emits progress updates that drive a
QProgressBar. On success, writes the TSV + meta sidecar and closes;
on failure, leaves the dialog open with the error so the user can see
what happened before retrying.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ddb.flybase.catalog import (
    CatalogMeta,
    CatalogPaths,
    DownloadProgress,
    ReleaseInfo,
    discover_current_release,
    download_catalog,
)


class _DownloadWorker(QThread):
    """Worker: discovers the release (if not pre-supplied), then downloads."""

    progress = Signal(object)  # DownloadProgress
    finished_ok = Signal(object)  # CatalogMeta on success, str on error
    failed = Signal(str)

    def __init__(self, paths: CatalogPaths, release: ReleaseInfo | None) -> None:
        super().__init__()
        self._paths = paths
        self._release = release

    def run(self) -> None:
        try:
            info = self._release or discover_current_release()
            meta = download_catalog(
                self._paths,
                release_info=info,
                on_progress=lambda p: self.progress.emit(p),
            )
            self.finished_ok.emit(meta)
        except Exception as e:  # noqa: BLE001 — surface any failure to the UI
            self.failed.emit(f"{type(e).__name__}: {e}")


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "—"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


class FlybaseDownloadDialog(QDialog):
    """Modal progress dialog. `meta` is populated on successful close."""

    def __init__(
        self,
        paths: CatalogPaths,
        release: ReleaseInfo | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download FlyBase catalog")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.meta: CatalogMeta | None = None
        self._paths = paths

        self.headline = QLabel("Preparing…")
        self.headline.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # indeterminate until first progress event
        self.detail = QLabel("")
        self.detail.setStyleSheet("color: #666;")

        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.close_btn)

        lo = QVBoxLayout(self)
        lo.addWidget(self.headline)
        lo.addWidget(self.bar)
        lo.addWidget(self.detail)
        lo.addLayout(btn_row)

        self._worker = _DownloadWorker(paths, release)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, p: DownloadProgress) -> None:
        if p.bytes_total > 0 and self.bar.maximum() == 0:
            self.bar.setRange(0, p.bytes_total)
        if p.bytes_total > 0:
            self.bar.setValue(p.bytes_done)
        self.headline.setText("Downloading catalog…")
        self.detail.setText(f"{_fmt_bytes(p.bytes_done)} / {_fmt_bytes(p.bytes_total)}")

    def _on_finished(self, meta: CatalogMeta) -> None:
        self.meta = meta
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.headline.setText(f"Downloaded release {meta.release}.")
        self.detail.setText(
            f"{meta.row_count:,} stocks across {len(meta.collection_counts)} collections · "
            f"{_fmt_bytes(meta.bytes)}."
        )
        self.close_btn.setEnabled(True)
        self.close_btn.setText("Done")
        self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self.accept)

    def _on_failed(self, message: str) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.headline.setText("Download failed.")
        self.detail.setText(message)
        self.detail.setStyleSheet("color: #a00;")
        self.close_btn.setEnabled(True)
