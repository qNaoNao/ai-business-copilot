from pathlib import Path
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "business.db"
)


def get_connection():
    """
    Create and return a connection to the SQLite database.
    """

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    return sqlite3.connect(DB_PATH)