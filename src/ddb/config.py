from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DDB_", extra="ignore")

    database_url: str = "sqlite:///./ddb.sqlite3"
    data_dir: Path = Path("./data")
    # Stamped into every QR payload ("db=" field). Change if you ever
    # federate with another DB install so scans can be routed.
    database_id: str = "local"


settings = Settings()
