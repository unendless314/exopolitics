import contextlib
import datetime
import os
import pathlib
import re
import sqlite3
from typing import List, Dict, Any, Optional

def get_utc_now_iso8601() -> str:
    """Returns the current UTC time formatted exactly as YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def get_connection(db_path: pathlib.Path) -> sqlite3.Connection:
    """
    Establishes a SQLite connection and enforces foreign keys immediately.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """
    Enforces a strict transaction boundary using BEGIN IMMEDIATE.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

def split_sql_statements(sql_content: str) -> List[str]:
    """
    Safely splits a SQL script into simple statements by semicolon.
    Raises ValueError (fail-fast) if complex SQL procedural keywords (TRIGGERS, VIEWS,
    explicit TRANSACTIONS) are detected, since these cannot be safely split
    by semicolon and are not allowed in classify migrations under Option A.
    """
    sanitized_for_scan = _strip_sql_comments_and_literals(sql_content).upper()
    forbidden_patterns = (
        r"\bCREATE\s+TRIGGER\b",
        r"\bCREATE\s+VIEW\b",
        r"\bBEGIN\b",
        r"\bCOMMIT\b",
        r"\bROLLBACK\b"
    )
    for pattern in forbidden_patterns:
        match = re.search(pattern, sanitized_for_scan)
        if match:
            matched_kw = match.group(0)
            raise ValueError(f"Complex SQL procedural statement or explicit transaction keyword '{matched_kw}' is not supported by this migration runner.")

    # Strip block comments /* ... */
    cleaned = re.sub(r"/\*.*?\*/", "", sql_content, flags=re.DOTALL)
    
    current_statement_lines = []
    statements = []
    
    for line in cleaned.splitlines():
        # Remove line-level comments starting with --
        line_clean = line.split("--", 1)[0].strip()
        if line_clean:
            current_statement_lines.append(line_clean)
            
    # Reassemble and split by semicolon
    full_content = " ".join(current_statement_lines)
    parts = full_content.split(";")
    for part in parts:
        stmt = part.strip()
        if stmt:
            statements.append(stmt)
            
    return statements

def _strip_sql_comments_and_literals(sql_content: str) -> str:
    """Removes comments and string literals so keyword scanning only sees SQL structure."""
    result = []
    i = 0
    length = len(sql_content)

    while i < length:
        ch = sql_content[i]
        nxt = sql_content[i + 1] if i + 1 < length else ""

        if ch == "-" and nxt == "-":
            i += 2
            while i < length and sql_content[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < length and not (sql_content[i] == "*" and sql_content[i + 1] == "/"):
                i += 1
            i = min(i + 2, length)
            continue

        if ch == "'":
            result.append(" ")
            i += 1
            while i < length:
                if sql_content[i] == "'":
                    if i + 1 < length and sql_content[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)

def run_migrations(db_path: pathlib.Path, migrations_dir: pathlib.Path) -> None:
    """
    Applies DDL scripts sequentially and tracks state in the schema_migrations table.
    Ensures re-entrancy and idempotence.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        conn.commit()

        if not migrations_dir.exists():
            return

        migration_files = sorted(migrations_dir.glob("*.sql"))
        for file in migration_files:
            migration_name = file.name

            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM schema_migrations WHERE migration_name = ?", (migration_name,))
            if cursor.fetchone():
                continue

            with transaction(conn):
                sql_content = file.read_text(encoding="utf-8")
                statements = split_sql_statements(sql_content)
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (migration_name, applied_at) VALUES (?, ?)",
                    (migration_name, get_utc_now_iso8601())
                )
    finally:
        conn.close()

class ClassificationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_pending_items(self, limit: int) -> List[Dict[str, Any]]:
        """
        Retrieves unclassified source_item rows that have been ingested but
        do not yet have a classification_result.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT s.source_item_id, s.title, s.summary, s.published_at, s.canonical_url
            FROM source_item s
            LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
            WHERE s.ingest_status = 'ingested'
              AND c.classification_result_id IS NULL
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def save_classification_result(self, result: Dict[str, Any]) -> None:
        """
        Persists a classification result to the database using an upsert to support
        the overwrite rule (updating existing classification for a given source_item_id).
        """
        # Ensure correct keys and convert to SQLite types (edit_candidate as 0 or 1)
        now = get_utc_now_iso8601()
        
        # Determine classified_at timestamp (default to now if not provided)
        classified_at = result.get("classified_at") or now
        created_at = result.get("created_at") or now

        # Map edit_candidate to exactly 0 or 1
        edit_candidate = 0
        raw_edit = result.get("edit_candidate")
        if raw_edit in (1, 1.0, True, "1", "true", "True"):
            edit_candidate = 1

        self.conn.execute(
            """
            INSERT INTO classification_result (
                source_item_id,
                topic_class,
                classification_reason,
                classification_confidence,
                edit_candidate,
                model_name,
                prompt_version,
                classified_at,
                created_at
            ) VALUES (
                :source_item_id,
                :topic_class,
                :classification_reason,
                :classification_confidence,
                :edit_candidate,
                :model_name,
                :prompt_version,
                :classified_at,
                :created_at
            )
            ON CONFLICT(source_item_id) DO UPDATE SET
                topic_class = excluded.topic_class,
                classification_reason = excluded.classification_reason,
                classification_confidence = excluded.classification_confidence,
                edit_candidate = excluded.edit_candidate,
                model_name = excluded.model_name,
                prompt_version = excluded.prompt_version,
                classified_at = excluded.classified_at,
                created_at = excluded.created_at
            """,
            {
                "source_item_id": result["source_item_id"],
                "topic_class": result["topic_class"],
                "classification_reason": result.get("classification_reason"),
                "classification_confidence": result.get("classification_confidence"),
                "edit_candidate": edit_candidate,
                "model_name": result["model_name"],
                "prompt_version": result["prompt_version"],
                "classified_at": classified_at,
                "created_at": created_at
            }
        )
