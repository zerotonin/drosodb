from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DDB_", extra="ignore")

    database_url: str = "sqlite:///./ddb.sqlite3"
    data_dir: Path = Path("./data")
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
    # Auto-print on vial create/flip from the GUI. Off by default so a
    # misconfigured printer can't silently spam labels.
    printer_auto_print: bool = False

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


settings = Settings()
