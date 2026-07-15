import sqlite3
import pathlib
import sys
import logging
from typing import Any, Union, Dict, Tuple, Optional

logger = logging.getLogger("modules.analysis.database")

def get_connection(db_path: pathlib.Path, timeout_ms: int = 10000) -> sqlite3.Connection:
    """
    Establishes a SQLite connection with a parameterized busy timeout.
    Enforces foreign keys and row factory.
    """
    try:
        timeout_sec = timeout_ms / 1000.0
        conn = sqlite3.connect(str(db_path), timeout=timeout_sec)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(f"PRAGMA busy_timeout = {timeout_ms};")
        return conn
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            logger.warning(f"Database lock error during connection: {e}. Exiting with code 1.")
            sys.exit(1)
        raise

def safe_execute(
    conn_or_cursor: Union[sqlite3.Connection, sqlite3.Cursor],
    sql: str,
    params: Optional[Union[Dict[str, Any], Tuple[Any, ...]]] = None
) -> sqlite3.Cursor:
    """
    Executes a SQL statement, catching SQLite busy/lock OperationalErrors
    to log a warning and exit with code 1.
    """
    try:
        if params is None:
            return conn_or_cursor.execute(sql)
        return conn_or_cursor.execute(sql, params)
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            logger.warning(f"Database lock error during query execution: {e}. Exiting with code 1.")
            sys.exit(1)
        raise
