from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..database import connect


@dataclass(slots=True)
class CommandContext:
    args: argparse.Namespace
    state_dir: Path
    database_path: Path
    connection: sqlite3.Connection

    @classmethod
    def create(cls, args: argparse.Namespace) -> CommandContext:
        state_dir = args.state.resolve()
        database_path = state_dir / "atlas.sqlite"
        return cls(args, state_dir, database_path, connect(database_path))

    def close(self) -> None:
        self.connection.close()

    def close_quietly(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def reconnect(self) -> sqlite3.Connection:
        self.connection = connect(self.database_path)
        return self.connection
