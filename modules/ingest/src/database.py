import contextlib
import datetime
import pathlib
import re
import sqlite3
from typing import List, Dict, Any, Optional

def get_utc_now_iso8601() -> str:
    """Returns the current UTC time formatted exactly as YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def get_connection(db_path: pathlib.Path) -> sqlite3.Connection:
    """
    Connection factory that establishes a SQLite connection.
    Guarantees that PRAGMA foreign_keys = ON; is executed immediately.
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
    Ensures rollback on error and clean commit on success.
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
    Safely splits a SQL script into individual complete statements.
    Uses sqlite3.complete_statement to correctly respect statement boundaries.
    """
    statements = []
    buffer = []
    
    for line in sql_content.splitlines():
        buffer.append(line)
        combined = "\n".join(buffer).strip()
        
        if not combined:
            buffer.clear()
            continue
            
        if sqlite3.complete_statement(combined):
            if _has_executable_sql(combined):
                statements.append(combined)
            buffer.clear()
            
    remaining = "\n".join(buffer).strip()
    if remaining and _has_executable_sql(remaining):
        statements.append(remaining)
        
    return statements

def _has_executable_sql(statement: str) -> bool:
    """Returns True when the buffered text contains executable SQL, not only comments."""
    stripped = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL)
    lines = []
    for line in stripped.splitlines():
        lines.append(line.split("--", 1)[0])
    return bool("\n".join(lines).strip())

def run_migrations(db_path: pathlib.Path, migrations_dir: pathlib.Path) -> None:
    """
    Re-entrant, idempotent schema migration runner.
    Applies DDL scripts sequentially and tracks state in the schema_migrations table.
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


class SourceStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, source_id: int) -> Optional[sqlite3.Row]:
        """Fetches the state of a single source."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM source_state WHERE source_id = ?", (source_id,))
        return cursor.fetchone()

    def upsert(self, source_id: int, state_data: Dict[str, Any]) -> None:
        """Upserts the mutable state record for a source."""
        now = get_utc_now_iso8601()
        
        fields = {
            "last_fetch_at": state_data.get("last_fetch_at"),
            "last_success_at": state_data.get("last_success_at"),
            "last_http_status": state_data.get("last_http_status"),
            "etag": state_data.get("etag"),
            "last_modified": state_data.get("last_modified"),
            "consecutive_failures": state_data.get("consecutive_failures", 0),
            "last_error_class": state_data.get("last_error_class"),
            "last_error_at": state_data.get("last_error_at"),
            "health_status": state_data.get("health_status", "healthy"),
            "quarantine_until": state_data.get("quarantine_until"),
            "updated_at": now
        }

        self.conn.execute("""
            INSERT INTO source_state (
                source_id, last_fetch_at, last_success_at, last_http_status,
                etag, last_modified, consecutive_failures, last_error_class,
                last_error_at, health_status, quarantine_until, updated_at
            ) VALUES (
                :source_id, :last_fetch_at, :last_success_at, :last_http_status,
                :etag, :last_modified, :consecutive_failures, :last_error_class,
                :last_error_at, :health_status, :quarantine_until, :updated_at
            )
            ON CONFLICT(source_id) DO UPDATE SET
                last_fetch_at = excluded.last_fetch_at,
                last_success_at = excluded.last_success_at,
                last_http_status = excluded.last_http_status,
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                consecutive_failures = excluded.consecutive_failures,
                last_error_class = excluded.last_error_class,
                last_error_at = excluded.last_error_at,
                health_status = excluded.health_status,
                quarantine_until = excluded.quarantine_until,
                updated_at = excluded.updated_at
        """, {"source_id": source_id, **fields})


class FetchRunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_scope: str, trigger_type: str, due_source_count: int) -> int:
        """Inserts a new fetch run record with initial status 'running' and returns its ID."""
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO fetch_run (
                started_at, run_scope, trigger_type, run_status, due_source_count
            ) VALUES (?, ?, ?, 'running', ?)
        """, (now, run_scope, trigger_type, due_source_count))
        return cursor.lastrowid

    def update_completion(
        self,
        fetch_run_id: int,
        run_status: str,
        attempted: int,
        succeeded: int,
        failed: int,
        error_summary: Optional[str] = None
    ) -> None:
        """Updates the status and counts of a run upon completion."""
        now = get_utc_now_iso8601()
        self.conn.execute("""
            UPDATE fetch_run
            SET ended_at = ?,
                run_status = ?,
                attempted_source_count = ?,
                succeeded_source_count = ?,
                failed_source_count = ?,
                error_summary = ?
            WHERE fetch_run_id = ?
        """, (now, run_status, attempted, succeeded, failed, error_summary, fetch_run_id))


class FetchAttemptRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, attempt_data: Dict[str, Any]) -> int:
        """Inserts a fetch attempt record."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO fetch_attempt (
                fetch_run_id, source_id, started_at, ended_at,
                retry_count, http_status, error_class, error_detail, outcome,
                new_item_count, dedup_matched_count, low_context_count,
                sanitization_failure_count, normalization_failure_count
            ) VALUES (
                :fetch_run_id, :source_id, :started_at, :ended_at,
                :retry_count, :http_status, :error_class, :error_detail, :outcome,
                :new_item_count, :dedup_matched_count, :low_context_count,
                :sanitization_failure_count, :normalization_failure_count
            )
        """, {
            "fetch_run_id": attempt_data["fetch_run_id"],
            "source_id": attempt_data["source_id"],
            "started_at": attempt_data["started_at"],
            "ended_at": attempt_data.get("ended_at"),
            "retry_count": attempt_data.get("retry_count", 0),
            "http_status": attempt_data.get("http_status"),
            "error_class": attempt_data.get("error_class"),
            "error_detail": attempt_data.get("error_detail"),
            "outcome": attempt_data["outcome"],
            "new_item_count": attempt_data.get("new_item_count", 0),
            "dedup_matched_count": attempt_data.get("dedup_matched_count", 0),
            "low_context_count": attempt_data.get("low_context_count", 0),
            "sanitization_failure_count": attempt_data.get("sanitization_failure_count", 0),
            "normalization_failure_count": attempt_data.get("normalization_failure_count", 0)
        })
        return cursor.lastrowid


class SourceItemRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, item_data: Dict[str, Any]) -> int:
        """Inserts a normalized canonical feed entry."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO source_item (
                source_id, source_item_guid, canonical_url, title,
                published_at, fetched_at, ingest_dedup_key, dedup_rule, ingest_status
            ) VALUES (
                :source_id, :source_item_guid, :canonical_url, :title,
                :published_at, :fetched_at, :ingest_dedup_key, :dedup_rule, 'ingested'
            )
        """, {
            "source_id": item_data["source_id"],
            "source_item_guid": item_data.get("source_item_guid"),
            "canonical_url": item_data.get("canonical_url"),
            "title": item_data["title"],
            "published_at": item_data.get("published_at"),
            "fetched_at": item_data.get("fetched_at"),
            "ingest_dedup_key": item_data["ingest_dedup_key"],
            "dedup_rule": item_data["dedup_rule"]
        })
        return cursor.lastrowid


class SourceItemTextRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, text_data: Dict[str, Any]) -> int:
        """Inserts a sanitized working text record."""
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method,
                html_detected, was_truncated, is_low_context, low_context_reason,
                raw_text_length, sanitized_text_length, reduction_ratio,
                created_at, updated_at
            ) VALUES (
                :source_item_id, :sanitized_text, :sanitization_method,
                :html_detected, :was_truncated, :is_low_context, :low_context_reason,
                :raw_text_length, :sanitized_text_length, :reduction_ratio,
                :created_at, :updated_at
            )
        """, {
            "source_item_id": text_data["source_item_id"],
            "sanitized_text": text_data["sanitized_text"],
            "sanitization_method": text_data["sanitization_method"],
            "html_detected": 1 if text_data.get("html_detected") else 0,
            "was_truncated": 1 if text_data.get("was_truncated") else 0,
            "is_low_context": 1 if text_data.get("is_low_context") else 0,
            "low_context_reason": text_data.get("low_context_reason"),
            "raw_text_length": text_data.get("raw_text_length"),
            "sanitized_text_length": text_data["sanitized_text_length"],
            "reduction_ratio": text_data.get("reduction_ratio"),
            "created_at": text_data.get("created_at", now),
            "updated_at": text_data.get("updated_at", now)
        })
        return cursor.lastrowid


class SourceItemRawRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, raw_data: Dict[str, Any]) -> int:
        """Inserts a raw retained payload record."""
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO source_item_raw (
                source_item_id, raw_payload, retention_class, expires_at, created_at
            ) VALUES (
                :source_item_id, :raw_payload, :retention_class, :expires_at, :created_at
            )
        """, {
            "source_item_id": raw_data["source_item_id"],
            "raw_payload": raw_data["raw_payload"],
            "retention_class": raw_data["retention_class"],
            "expires_at": raw_data.get("expires_at"),
            "created_at": raw_data.get("created_at", now)
        })
        return cursor.lastrowid


class DedupMarkerRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def exists(self, dedup_key: str) -> bool:
        """Checks if a dedup key exists."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM ingest_dedup_marker WHERE dedup_key = ?", (dedup_key,))
        return cursor.fetchone() is not None

    def insert(self, dedup_key: str, dedup_rule: str, source_item_id: int) -> None:
        """Registers a unique dedup marker constraint."""
        now = get_utc_now_iso8601()
        self.conn.execute("""
            INSERT INTO ingest_dedup_marker (
                dedup_key, dedup_rule, source_item_id, created_at
            ) VALUES (?, ?, ?, ?)
        """, (dedup_key, dedup_rule, source_item_id, now))
