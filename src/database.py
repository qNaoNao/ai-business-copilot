from pathlib import Path
import os
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

    database_path = Path(os.getenv("BUSINESS_DB_PATH") or DB_PATH).expanduser().resolve()
    if not database_path.exists():
        raise FileNotFoundError(
            f"Database not found: {database_path}. Run python -m src.demo_data for a synthetic sample."
        )

    return sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
