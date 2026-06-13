import contextlib
import datetime
import json
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
def transaction(conn: sqlite3.Connection, commit: bool = True):
    """
    Enforces a strict transaction boundary using BEGIN IMMEDIATE.
    Ensures rollback on error and clean commit on success.
    Also supports dry-run mode where commit is suppressed.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise

def split_sql_statements(sql_content: str) -> List[str]:
    """
    Safely splits a SQL script into individual complete statements.
    Uses sqlite3.complete_statement to respect statement boundaries.
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
    Re-entrant, idempotent schema migration runner for classification tables.
    Tracks state in the schema_migrations table.
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
            
            with transaction(conn, commit=True):
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


class ClassificationResultRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_pending_items(self, limit: int = 20) -> List[sqlite3.Row]:
        """
        Retrieves pending unclassified items that have sanitized text.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT 
                s.source_item_id, 
                s.title, 
                t.sanitized_text, 
                t.is_low_context,
                t.low_context_reason
            FROM source_item s
            JOIN source_item_text t ON s.source_item_id = t.source_item_id
            LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
            WHERE s.ingest_status = 'ingested'
              AND c.classification_result_id IS NULL
            LIMIT ?;
        """
        cursor.execute(query, (limit,))
        return cursor.fetchall()

    def upsert(self, result_data: Dict[str, Any]) -> int:
        """
        Upserts a classification result using INSERT ... ON CONFLICT(source_item_id) DO UPDATE.
        This prevents row ID changes/surrogate key resets on reclassification.
        """
        now = get_utc_now_iso8601()
        
        # Format additional_signals as string if it is a dictionary/list
        additional_signals = result_data.get("additional_signals")
        if isinstance(additional_signals, (dict, list)):
            additional_signals = json.dumps(additional_signals)

        fields = {
            "source_item_id": result_data["source_item_id"],
            "topic_class": result_data["topic_class"],
            "classification_reason": result_data.get("classification_reason"),
            "classification_confidence": result_data.get("classification_confidence"),
            "content_density": result_data.get("content_density"),
            "source_text_quality": result_data.get("source_text_quality"),
            "primary_language_code": result_data.get("primary_language_code"),
            "governmental_involvement": result_data.get("governmental_involvement"),
            "additional_signals": additional_signals,
            "model_name": result_data["model_name"],
            "prompt_version": result_data["prompt_version"],
            "classified_at": result_data.get("classified_at", now),
            "created_at": result_data.get("created_at", now)
        }

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO classification_result (
                source_item_id, topic_class, classification_reason, classification_confidence,
                content_density, source_text_quality, primary_language_code,
                governmental_involvement, additional_signals, model_name, prompt_version,
                classified_at, created_at
            ) VALUES (
                :source_item_id, :topic_class, :classification_reason, :classification_confidence,
                :content_density, :source_text_quality, :primary_language_code,
                :governmental_involvement, :additional_signals, :model_name, :prompt_version,
                :classified_at, :created_at
            )
            ON CONFLICT(source_item_id) DO UPDATE SET
                topic_class = excluded.topic_class,
                classification_reason = excluded.classification_reason,
                classification_confidence = excluded.classification_confidence,
                content_density = excluded.content_density,
                source_text_quality = excluded.source_text_quality,
                primary_language_code = excluded.primary_language_code,
                governmental_involvement = excluded.governmental_involvement,
                additional_signals = excluded.additional_signals,
                model_name = excluded.model_name,
                prompt_version = excluded.prompt_version,
                classified_at = excluded.classified_at
        """, fields)
        return cursor.lastrowid
