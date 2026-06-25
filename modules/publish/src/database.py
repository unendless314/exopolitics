import contextlib
import datetime
import pathlib
import sqlite3
import re
from typing import List, Dict, Any, Optional, Set, Tuple

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
    Re-entrant, idempotent schema migration runner for publish tables.
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


class PublishRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_reconciliation_candidates(self) -> List[sqlite3.Row]:
        """
        Query mother-draft records, metadata, translations and curation status.
        Does NOT query the t.content body to preserve memory under constraints.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT
                a.parent_content_id,
                a.source_item_id,
                a.content_fingerprint,
                a.approved_at,
                a.author_metadata,
                t.language_code,
                t.display_title,
                t.source_fingerprint,
                t.translated_at,
                c.curate_status,
                c.downstream_action,
                s.canonical_url,
                s.published_at AS source_published_at,
                pr.publish_record_id,
                pr.slug,
                pls.publish_language_status_id,
                pls.publish_status,
                pls.source_fingerprint AS published_source_fingerprint
            FROM approved_content_record a
            JOIN curation_decision c
                ON c.source_item_id = a.source_item_id
            JOIN translation_output t
                ON t.parent_content_id = a.parent_content_id
               AND t.source_fingerprint = a.content_fingerprint
            JOIN source_item s
                ON s.source_item_id = a.source_item_id
            LEFT JOIN publish_record pr
                ON pr.source_item_id = a.source_item_id
            LEFT JOIN publish_language_status pls
                ON pls.publish_record_id = pr.publish_record_id
               AND pls.language_code = t.language_code
            WHERE c.curate_status = 'approved'
              AND t.translation_status = 'completed';
        """
        cursor.execute(query)
        return cursor.fetchall()

    def get_active_publish_statuses(self) -> List[sqlite3.Row]:
        """
        Query all existing publish records and their language statuses.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT
                pr.source_item_id,
                pr.slug,
                pls.language_code,
                pls.publish_status,
                pls.source_fingerprint,
                pls.published_at,
                pls.withdrawn_at,
                pls.publish_record_id,
                pls.publish_language_status_id
            FROM publish_record pr
            JOIN publish_language_status pls
                ON pls.publish_record_id = pr.publish_record_id;
        """
        cursor.execute(query)
        return cursor.fetchall()

    def get_publish_record_by_source_item_id(self, source_item_id: int) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM publish_record WHERE source_item_id = ?", (source_item_id,))
        return cursor.fetchone()

    def insert_publish_record(self, source_item_id: int, slug: str, first_published_at: str) -> int:
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO publish_record (source_item_id, slug, first_published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (source_item_id, slug, first_published_at, now, now))
        return cursor.lastrowid

    def update_publish_record_updated_at(self, publish_record_id: int, updated_at: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE publish_record
            SET updated_at = ?
            WHERE publish_record_id = ?
        """, (updated_at, publish_record_id))

    def get_publish_language_status(self, publish_record_id: int, language_code: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM publish_language_status
            WHERE publish_record_id = ? AND language_code = ?
        """, (publish_record_id, language_code))
        return cursor.fetchone()

    def upsert_publish_language_status(
        self,
        publish_record_id: int,
        language_code: str,
        publish_status: str,
        published_at: Optional[str],
        withdrawn_at: Optional[str],
        source_fingerprint: str
    ) -> None:
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO publish_language_status (
                publish_record_id, language_code, publish_status, published_at, withdrawn_at, source_fingerprint, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT (publish_record_id, language_code) DO UPDATE SET
                publish_status = excluded.publish_status,
                published_at = CASE WHEN excluded.publish_status = 'published' THEN excluded.published_at ELSE published_at END,
                withdrawn_at = CASE WHEN excluded.publish_status = 'withdrawn' THEN excluded.withdrawn_at ELSE withdrawn_at END,
                source_fingerprint = excluded.source_fingerprint
        """, (publish_record_id, language_code, publish_status, published_at, withdrawn_at, source_fingerprint, now))

    def get_all_frozen_slugs(self) -> Set[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT slug FROM publish_record")
        return {row["slug"] for row in cursor.fetchall()}

    def fetch_canonical_item_payload(self, source_item_id: int, language_code: str) -> Optional[sqlite3.Row]:
        """
        Fetch canonical fields required for publishing directly from upstream tables,
        without joining publish_record or publish_language_status.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT
                a.source_item_id,
                t.language_code,
                t.display_title,
                t.content,
                s.canonical_url,
                s.published_at AS source_published_at,
                a.approved_at,
                c.downstream_action,
                a.author_metadata
            FROM approved_content_record a
            JOIN curation_decision c ON c.source_item_id = a.source_item_id
            JOIN translation_output t ON t.parent_content_id = a.parent_content_id AND t.source_fingerprint = a.content_fingerprint
            JOIN source_item s ON s.source_item_id = a.source_item_id
            WHERE a.source_item_id = ? AND t.language_code = ?;
        """
        cursor.execute(query, (source_item_id, language_code))
        return cursor.fetchone()

