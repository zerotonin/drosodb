from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor DB, data, and .env at the package root so `ddb gui` / `ddb vial create`
# do the right thing regardless of the caller's cwd. Walking three parents up
# from `src/ddb/config.py` lands at the project root where the editable install
# lives. Callers who want a different location can always override via env vars.
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Project-wide configuration, populated from env vars and `.env`.

    Every field is overridable via `DDB_<UPPERCASE_NAME>` in `.env`
    (e.g. `DDB_PRINTER_ENABLED=1`). Fields are grouped informally below
    by subsystem: database, printer, GUI, camera. Callers should import
    the module-level `settings` singleton rather than constructing
    their own.
    """

    model_config = SettingsConfigDict(
        env_file=_PKG_ROOT / ".env", env_prefix="DDB_", extra="ignore"
    )

    database_url: str = f"sqlite:///{_PKG_ROOT / 'ddb.sqlite3'}"
    data_dir: Path = _PKG_ROOT / "data"
    # Stamped into every QR payload ("db=" field). Change if you ever
    # federate with another DB install so scans can be routed.
    database_id: str = "local"

    # --- Printer ---
    # Disabled by default so dev/test machines don't try to reach hardware.
    # Set DDB_PRINTER_ENABLED=1 in your .env once the printer is configured.
    printer_enabled: bool = False
    # One of: "bluetooth", "network", "file"
    printer_backend: str = "bluetooth"
    printer_model: str = "QL-820NWB"
    # brother_ql label identifier (matches the loaded DK roll):
    #   DK-11204 -> "17x54"   (DDB default)
    #   DK-11201 -> "29x90"
    #   DK-22205 -> "62"      (continuous 62mm)
    printer_label: str = "17x54"
    # Auto-print on vial create/flip from the GUI. On by default whenever
    # the printer is enabled — the common case is "new vial → label in hand".
    # Set DDB_PRINTER_AUTO_PRINT=0 to opt out (the checkbox is still there
    # so individual vials can skip printing ad-hoc).
    printer_auto_print: bool = True

    # Bluetooth backend
    printer_bluetooth_mac: str | None = None
    printer_bluetooth_channel: int = 1
    # Our conda Python lacks AF_BLUETOOTH; we shell out to this interpreter.
    printer_system_python: Path = Path("/usr/bin/python3")

    # Network backend (TCP :9100, Brother raw print)
    printer_network_host: str | None = None
    printer_network_port: int = 9100

    # File backend (debugging / offline prep). Defaults to data_dir/print_jobs.
    printer_file_dir: Path | None = None

    # --- GUI defaults (reduce friction when creating vials) ---
    # The New Vial dialog pre-selects these so biologists can just pick a
    # genotype and hit OK. Both are created on first use if they don't exist.
    default_org_unit: str = "Geurten lab stock"
    default_owner_username: str = "stockkeeper"
    default_owner_full_name: str = "Stock keeper"

    # Set DDB_GUI_DEBUG=1 to expose the Scan tab's "Save snapshot" button
    # (writes the current frame + decode report under data/snapshots/).
    # Off by default so biologists aren't confronted with debug buttons.
    gui_debug: bool = False

    # Multiplier applied to the GUI font size at startup. 1.0 = system
    # default (~10pt on Linux). Users can bump this via the Settings tab
    # or live with Ctrl+= / Ctrl+- (Ctrl+0 resets). Clamped to [0.7, 2.0]
    # — below 0.7 the labels collapse, above 2.0 the dialogs overflow on
    # most laptop displays.
    gui_font_scale: float = 1.0

    # Which camera role the Scan tab starts on. Users can still switch
    # per-session via the combo; this is just the first-run default.
    default_camera_role: str = "back"

    # Audible "ping" (bundled 1-Up WAV) on every successful QR decode.
    # Helpful in a noisy lab where the user isn't always looking at the
    # screen while passing vials under the camera. Set DDB_SCAN_SOUND=0
    # to mute. Playback uses `aplay`; if it's missing the chirp silently
    # no-ops.
    scan_sound: bool = True

    # --- FlyBase genotype catalog ---
    # Off by default so a first-run install doesn't phone home. Flipping
    # this on enables the Settings-tab "Genotype catalog" group and the
    # Genotypes-tab "Import…" button.
    flybase_enabled: bool = False
    # "manual" | "weekly" | "monthly" — how aggressive the startup
    # refresh check should be when the local catalog is older than the
    # corresponding threshold.
    flybase_refresh_mode: str = "manual"


settings = Settings()
