# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.update_group                                          ║
# ║  « check for updates and pull them from the GUI »                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Settings-tab box: version label, Check / Update buttons and     ║
# ║  the list of incoming commits. Git and pip run in QThreads so    ║
# ║  the window stays responsive during a slow fetch or reinstall.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Settings-tab "Updates" box backed by :mod:`ddb.updater`."""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QProcess, QThread, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ddb.updater import (
    REPO_ROOT,
    UpdateError,
    UpdateResult,
    UpdateStatus,
    apply_update,
    check_for_update,
    check_schema,
    describe_local_version,
    restart_command,
)

ADMIN_UPGRADE_HINT = (
    "Ask the tablet admin to run <code>alembic upgrade head</code> in the DDB "
    "folder once everyone has closed DDB."
)

# ┌────────────────────────────────────────────────────────────┐
# │ Workers  « git + pip off the GUI thread »                  │
# └────────────────────────────────────────────────────────────┘


class _Worker(QThread):
    """Run one updater call; report its value or the UpdateError text."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[], object]) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            self.succeeded.emit(self._job())
        except UpdateError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 — surface anything else to the UI
            self.failed.emit(f"{type(e).__name__}: {e}")


# ┌────────────────────────────────────────────────────────────┐
# │ Settings box  « version, check, update, restart »          │
# └────────────────────────────────────────────────────────────┘


class UpdateGroup(QGroupBox):
    """Show the installed version, check upstream, fast-forward on request.

    Emits ``update_available(behind)`` after a check that found work to do,
    so the main window can flag it in the status bar. ``behind == 0`` means
    the code is current but the environment is missing packages.
    """

    update_available = Signal(int)

    def __init__(self, repo: Path = REPO_ROOT, parent: QWidget | None = None) -> None:
        super().__init__("Updates", parent)
        self._repo = repo
        self._worker: _Worker | None = None

        self.version_lbl = QLabel()
        self.state_lbl = QLabel("Not checked yet.")
        self.state_lbl.setWordWrap(True)
        self.incoming_lbl = QLabel()
        self.incoming_lbl.setWordWrap(True)
        self.incoming_lbl.hide()
        self.schema_lbl = QLabel()
        self.schema_lbl.setWordWrap(True)

        self.check_btn = QPushButton("Check for updates")
        self.check_btn.clicked.connect(self.check)
        self.update_btn = QPushButton("Update now")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self._on_update_clicked)

        buttons = QHBoxLayout()
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.update_btn)
        buttons.addStretch()

        lo = QVBoxLayout(self)
        lo.addWidget(self.version_lbl)
        lo.addLayout(buttons)
        lo.addWidget(self.state_lbl)
        lo.addWidget(self.incoming_lbl)
        lo.addWidget(self.schema_lbl)

        self._refresh_version_label()
        self._refresh_schema_label()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Start a background fetch + compare unless one is already running."""
        if self._is_busy():
            return
        self.state_lbl.setText("Checking for updates…")
        self._start(lambda: check_for_update(self._repo), self._on_checked, self._on_check_failed)

    def shutdown(self) -> None:
        """Block until a running git / pip step ends; call from closeEvent.

        Waiting (rather than abandoning the thread) keeps a pip reinstall
        from being killed half-way and avoids Qt aborting on a live QThread.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait()

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def _on_checked(self, status: UpdateStatus) -> None:
        self._set_buttons_enabled(True)
        self.update_btn.setEnabled(status.can_fast_forward)
        self._show_incoming(status)
        if status.is_up_to_date and not status.needs_reinstall:
            self.state_lbl.setText(f"Up to date with <b>{status.upstream}</b>.")
            return
        if status.is_up_to_date:
            text = (
                "Code is up to date, but this environment is missing packages: "
                f"<b>{html.escape(', '.join(status.unmet_requirements))}</b>. "
                "<i>Update now</i> installs them."
            )
        else:
            text = f"<b>{status.behind}</b> new update(s) on <b>{status.upstream}</b>."
        reason = status.blocking_reason()
        if reason is not None:
            text += f"<br><span style='color:#b00;'>{html.escape(reason)}</span>"
        elif status.needs_reinstall and not status.is_up_to_date:
            text += "<br><i>Needs new packages; updating installs them.</i>"
        self.state_lbl.setText(text)
        self.update_available.emit(status.behind)

    def _on_check_failed(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self.update_btn.setEnabled(False)
        self.state_lbl.setText(f"Could not check for updates: {html.escape(message)}")

    def _show_incoming(self, status: UpdateStatus) -> None:
        if not status.incoming:
            self.incoming_lbl.hide()
            return
        items = "".join(f"<li>{html.escape(line)}</li>" for line in status.incoming)
        more = status.behind - len(status.incoming)
        tail = f"<i>…and {more} more</i>" if more > 0 else ""
        self.incoming_lbl.setText(f"What's new:<ul style='margin:0;'>{items}</ul>{tail}")
        self.incoming_lbl.show()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def _on_update_clicked(self) -> None:
        if self._is_busy():
            return
        answer = QMessageBox.question(
            self,
            "Update DDB",
            "Download and install the latest DDB now?\n\n"
            "DDB needs a restart afterwards to use the new version.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state_lbl.setText("Updating… (a dependency reinstall can take a few minutes)")
        self._start(lambda: apply_update(self._repo), self._on_updated, self._on_update_failed)

    def _on_updated(self, result: UpdateResult) -> None:
        self._set_buttons_enabled(True)
        self.update_btn.setEnabled(False)
        self.incoming_lbl.hide()
        self._refresh_version_label()
        schema_warning = self._refresh_schema_label()
        self.state_lbl.setText(
            f"Updated {result.old_sha} → <b>{result.new_sha}</b>. Restart DDB to use it."
        )

        message = f"DDB was updated to {result.new_sha}."
        if result.reinstalled:
            message += "\nDependencies were reinstalled."
        if schema_warning:
            message += (
                "\n\nThe shared database needs a schema upgrade before the new "
                "version can use it. Ask the tablet admin to run "
                "'alembic upgrade head' once everyone has closed DDB."
            )
        message += "\n\nRestart DDB now?"
        answer = QMessageBox.question(self, "Update installed", message)
        if answer == QMessageBox.StandardButton.Yes:
            self._restart()

    def _on_update_failed(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self.update_btn.setEnabled(False)
        self._refresh_version_label()
        self.state_lbl.setText(f"Update failed: {html.escape(message)}")
        QMessageBox.warning(self, "Update failed", message)

    def _restart(self) -> None:
        program, *args = restart_command()
        if not QProcess.startDetached(program, args):
            QMessageBox.warning(self, "Restart failed", "Please close and reopen DDB yourself.")
            return
        self.window().close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_version_label(self) -> None:
        self.version_lbl.setText(f"Installed version: <b>{describe_local_version(self._repo)}</b>")

    def _refresh_schema_label(self) -> bool:
        """Update the schema line; return True when an admin upgrade is due."""
        try:
            from ddb.db import engine

            schema = check_schema(engine, self._repo)
        except Exception:  # noqa: BLE001 — informational only, never block the tab
            self.schema_lbl.hide()
            return False
        if schema.db_is_newer:
            self.schema_lbl.setText(
                "<span style='color:#b00;'>The shared database was upgraded by a "
                "newer DDB. Update this copy.</span>"
            )
        elif schema.needs_upgrade:
            self.schema_lbl.setText(
                f"<span style='color:#b00;'>Database schema is behind this version. "
                f"{ADMIN_UPGRADE_HINT}</span>"
            )
        else:
            self.schema_lbl.setText("<span style='color:#666;'>Database schema: current.</span>")
        self.schema_lbl.show()
        return schema.needs_upgrade

    def _start(
        self,
        job: Callable[[], object],
        on_success: Callable[..., None],
        on_failure: Callable[[str], None],
    ) -> None:
        self._set_buttons_enabled(False)
        worker = _Worker(job)
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        self._worker = worker
        worker.start()

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.check_btn.setEnabled(enabled)
        if not enabled:
            self.update_btn.setEnabled(False)
