import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(Path(os.environ.get("MISSION_CONTROL_DB_PATH", "data/mission_control.db")))
