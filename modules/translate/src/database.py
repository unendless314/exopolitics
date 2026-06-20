import contextlib
import datetime
import pathlib
import sqlite3
import re
from typing import List, Dict, Any, Optional, Tuple

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
    Re-entrant, idempotent schema migration runner for translate tables.
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


class TranslationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_approved_content_records(self, limit: int = 20) -> List[sqlite3.Row]:
        """Query mother-draft records from approved_content_record."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM approved_content_record LIMIT ?", (limit,))
        return cursor.fetchall()

    def get_approved_content_by_id(self, parent_content_id: int) -> Optional[sqlite3.Row]:
        """Fetch approved content record by parent_content_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM approved_content_record WHERE parent_content_id = ?", (parent_content_id,))
        return cursor.fetchone()

    def get_approved_content_by_source_id(self, source_item_id: int) -> Optional[sqlite3.Row]:
        """Fetch approved content record by source_item_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM approved_content_record WHERE source_item_id = ?", (source_item_id,))
        return cursor.fetchone()

    def get_translation_output(self, parent_content_id: int, language_code: str) -> Optional[sqlite3.Row]:
        """Fetch a translation output row by composite key."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM translation_output WHERE parent_content_id = ? AND language_code = ?",
            (parent_content_id, language_code)
        )
        return cursor.fetchone()

    def get_translation_output_by_id(self, translation_output_id: int) -> Optional[sqlite3.Row]:
        """Fetch translation output by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM translation_output WHERE translation_output_id = ?", (translation_output_id,))
        return cursor.fetchone()

    def upsert_translation_output(self, data: Dict[str, Any]) -> int:
        """Upserts a translation output record."""
        now = get_utc_now_iso8601()
        fields = {
            "parent_content_id": data["parent_content_id"],
            "source_item_id": data["source_item_id"],
            "language_code": data["language_code"],
            "display_title": data.get("display_title"),
            "content": data.get("content"),
            "source_fingerprint": data["source_fingerprint"],
            "translation_status": data["translation_status"],
            "retry_count": data.get("retry_count", 0),
            "model_name": data["model_name"],
            "prompt_version": data["prompt_version"],
            "translated_at": data.get("translated_at"),
            "updated_at": data.get("updated_at", now),
        }
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO translation_output (
                parent_content_id, source_item_id, language_code, display_title, content,
                source_fingerprint, translation_status, retry_count, model_name, prompt_version,
                translated_at, updated_at
            ) VALUES (
                :parent_content_id, :source_item_id, :language_code, :display_title, :content,
                :source_fingerprint, :translation_status, :retry_count, :model_name, :prompt_version,
                :translated_at, :updated_at
            )
            ON CONFLICT(parent_content_id, language_code) DO UPDATE SET
                source_item_id = excluded.source_item_id,
                display_title = excluded.display_title,
                content = excluded.content,
                source_fingerprint = excluded.source_fingerprint,
                translation_status = excluded.translation_status,
                retry_count = excluded.retry_count,
                model_name = excluded.model_name,
                prompt_version = excluded.prompt_version,
                translated_at = excluded.translated_at,
                updated_at = excluded.updated_at
        """, fields)
        return cursor.lastrowid

    def update_translation_status(self, parent_content_id: int, language_code: str, status: str, retry_count: Optional[int] = None) -> None:
        """Directly update status and optionally retry count for a translation output."""
        now = get_utc_now_iso8601()
        cursor = self.conn.cursor()
        if retry_count is not None:
            cursor.execute("""
                UPDATE translation_output
                SET translation_status = ?, retry_count = ?, updated_at = ?
                WHERE parent_content_id = ? AND language_code = ?
            """, (status, retry_count, now, parent_content_id, language_code))
        else:
            cursor.execute("""
                UPDATE translation_output
                SET translation_status = ?, updated_at = ?
                WHERE parent_content_id = ? AND language_code = ?
            """, (status, now, parent_content_id, language_code))

    def detect_and_mark_stale(self, running_model: str, running_prompt_version: str) -> List[Tuple[int, str, str]]:
        """
        Scans all translation outputs and compares against current approved content records
        and running config. Returns a list of (parent_content_id, language_code, reason) marked stale.
        Exception: Bypassed self-translations (model_name = 'bypass', prompt_version = 'bypass')
        are exempt from configuration stale check but subject to fingerprint mismatch checks.
        """
        cursor = self.conn.cursor()
        # Find fingerprint mismatches
        cursor.execute("""
            SELECT t.parent_content_id, t.language_code, t.translation_status
            FROM translation_output t
            JOIN approved_content_record a ON t.parent_content_id = a.parent_content_id
            WHERE t.source_fingerprint != a.content_fingerprint
              AND t.translation_status != 'stale'
        """)
        mismatch_stales = cursor.fetchall()
        
        # Find configuration mismatches for non-bypass records
        cursor.execute("""
            SELECT t.parent_content_id, t.language_code, t.translation_status
            FROM translation_output t
            WHERE t.translation_status = 'completed'
              AND NOT (t.model_name = 'bypass' AND t.prompt_version = 'bypass')
              AND (t.model_name != ? OR t.prompt_version != ?)
        """, (running_model, running_prompt_version))
        config_stales = cursor.fetchall()

        staled_items = []
        now = get_utc_now_iso8601()

        # Update mismatch stales
        for item in mismatch_stales:
            cursor.execute("""
                UPDATE translation_output
                SET translation_status = 'stale', updated_at = ?
                WHERE parent_content_id = ? AND language_code = ?
            """, (now, item["parent_content_id"], item["language_code"]))
            staled_items.append((item["parent_content_id"], item["language_code"], "fingerprint_mismatch"))

        # Update config stales
        for item in config_stales:
            cursor.execute("""
                UPDATE translation_output
                SET translation_status = 'stale', updated_at = ?
                WHERE parent_content_id = ? AND language_code = ?
            """, (now, item["parent_content_id"], item["language_code"]))
            staled_items.append((item["parent_content_id"], item["language_code"], "config_change"))

        return staled_items

    def get_pending_translation_tasks(self, target_languages: List[str], retry_attempts: int = 3) -> List[Dict[str, Any]]:
        """
        Finds items from approved_content_record that are eligible for translation in target languages.
        Returns a list of dicts with (approved_content_record fields + target_language code + status).
        Selection criteria:
        1. No matching row exists in translation_output for (parent_content_id, language_code).
        2. Status is 'pending'.
        3. Status is 'stale'.
        4. Status is 'failed' and retry_count < retry_attempts.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT 
                a.parent_content_id,
                a.source_item_id,
                a.display_title,
                a.content_body,
                a.content_fingerprint,
                a.content_language_code,
                a.approved_at,
                t.translation_output_id,
                t.language_code,
                t.source_fingerprint AS trans_source_fingerprint,
                t.translation_status,
                t.retry_count,
                t.model_name,
                t.prompt_version
            FROM approved_content_record a
            LEFT JOIN translation_output t ON a.parent_content_id = t.parent_content_id
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        # Build map parent_content_id -> {lang_code: row}
        records = {}
        for r in rows:
            pid = r["parent_content_id"]
            if pid not in records:
                records[pid] = {
                    "record": r,
                    "translations": {}
                }
            lang = r["language_code"]
            if lang:
                records[pid]["translations"][lang] = r
                
        tasks = []
        for pid, data in records.items():
            r = data["record"]
            for lang in target_languages:
                trans = data["translations"].get(lang)
                if not trans:
                    tasks.append({
                        "parent_content_id": r["parent_content_id"],
                        "source_item_id": r["source_item_id"],
                        "display_title": r["display_title"],
                        "content_body": r["content_body"],
                        "content_fingerprint": r["content_fingerprint"],
                        "content_language_code": r["content_language_code"],
                        "approved_at": r["approved_at"],
                        "language_code": lang,
                        "status": "new",
                        "retry_count": 0,
                    })
                else:
                    status = trans["translation_status"]
                    retry_cnt = trans["retry_count"]
                    if status in ("pending", "stale") or (status == "failed" and retry_cnt < retry_attempts):
                        tasks.append({
                            "parent_content_id": r["parent_content_id"],
                            "source_item_id": r["source_item_id"],
                            "display_title": r["display_title"],
                            "content_body": r["content_body"],
                            "content_fingerprint": r["content_fingerprint"],
                            "content_language_code": r["content_language_code"],
                            "approved_at": r["approved_at"],
                            "language_code": lang,
                            "status": status,
                            "retry_count": retry_cnt,
                        })
                        
        return tasks
