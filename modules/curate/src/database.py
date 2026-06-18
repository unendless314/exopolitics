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
    Supports dry-run mode where commit is suppressed.
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
    Re-entrant, idempotent schema migration runner for curation tables.
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


class CurationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_pending_items(self, limit: int = 20) -> List[sqlite3.Row]:
        """
        Retrieves pending uncurated items (or previously failed items that are eligible for retry)
        that have topic_class IN ('core', 'adjacent').
        """
        cursor = self.conn.cursor()
        query = """
            SELECT 
                s.source_item_id, 
                s.title AS raw_title, 
                s.canonical_url,
                t.sanitized_text, 
                c.topic_class,
                c.classification_reason,
                c.governmental_involvement
            FROM source_item s
            JOIN source_item_text t ON s.source_item_id = t.source_item_id
            JOIN classification_result c ON s.source_item_id = c.source_item_id
            LEFT JOIN curation_decision r ON s.source_item_id = r.source_item_id
            WHERE s.ingest_status = 'ingested'
              AND c.topic_class IN ('core', 'adjacent')
              AND (r.curation_decision_id IS NULL OR (r.curate_status = 'failed' AND r.retry_count < 3))
            LIMIT ?;
        """
        cursor.execute(query, (limit,))
        return cursor.fetchall()

    def get_item_by_id(self, source_item_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches a specific item by its source_item_id if it exists, topic class is core/adjacent,
        and ingest_status is ingested.
        """
        cursor = self.conn.cursor()
        query = """
            SELECT 
                s.source_item_id, 
                s.title AS raw_title, 
                s.canonical_url,
                t.sanitized_text, 
                c.topic_class,
                c.classification_reason,
                c.governmental_involvement
            FROM source_item s
            JOIN source_item_text t ON s.source_item_id = t.source_item_id
            JOIN classification_result c ON s.source_item_id = c.source_item_id
            WHERE s.source_item_id = ?
              AND s.ingest_status = 'ingested'
              AND c.topic_class IN ('core', 'adjacent');
        """
        cursor.execute(query, (source_item_id,))
        return cursor.fetchone()


    def get_curation_decision(self, source_item_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches curation decision for a source item if it exists.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM curation_decision WHERE source_item_id = ?",
            (source_item_id,)
        )
        return cursor.fetchone()

    def upsert_curation_decision(self, decision_data: Dict[str, Any]) -> int:
        """
        Upserts a curation decision using INSERT ... ON CONFLICT(source_item_id) DO UPDATE.
        """
        now = get_utc_now_iso8601()
        fields = {
            "source_item_id": decision_data["source_item_id"],
            "curate_status": decision_data["curate_status"],
            "downstream_action": decision_data.get("downstream_action"),
            "decision_reason": decision_data.get("decision_reason"),
            "retry_count": decision_data.get("retry_count", 0),
            "model_name": decision_data["model_name"],
            "prompt_version": decision_data["prompt_version"],
            "curated_at": decision_data.get("curated_at", now),
            "created_at": decision_data.get("created_at", now),
        }

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO curation_decision (
                source_item_id, curate_status, downstream_action, decision_reason,
                retry_count, model_name, prompt_version, curated_at, created_at
            ) VALUES (
                :source_item_id, :curate_status, :downstream_action, :decision_reason,
                :retry_count, :model_name, :prompt_version, :curated_at, :created_at
            )
            ON CONFLICT(source_item_id) DO UPDATE SET
                curate_status = excluded.curate_status,
                downstream_action = excluded.downstream_action,
                decision_reason = excluded.decision_reason,
                retry_count = excluded.retry_count,
                model_name = excluded.model_name,
                prompt_version = excluded.prompt_version,
                curated_at = excluded.curated_at
        """, fields)
        return cursor.lastrowid

    def upsert_editor_brief(self, brief_data: Dict[str, Any]) -> int:
        """
        Upserts an editor brief using INSERT ... ON CONFLICT(source_item_id) DO UPDATE.
        """
        now = get_utc_now_iso8601()
        risk_flags = brief_data.get("risk_flags")
        if isinstance(risk_flags, (dict, list)):
            risk_flags = json.dumps(risk_flags)

        fields = {
            "source_item_id": brief_data["source_item_id"],
            "brief_goal": brief_data["brief_goal"],
            "target_format": brief_data["target_format"],
            "key_claim": brief_data.get("key_claim"),
            "key_evidence": brief_data.get("key_evidence"),
            "required_context": brief_data.get("required_context"),
            "risk_flags": risk_flags,
            "tone_guidance": brief_data["tone_guidance"],
            "created_at": brief_data.get("created_at", now),
            "updated_at": brief_data.get("updated_at", now),
        }

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO editor_brief (
                source_item_id, brief_goal, target_format, key_claim, key_evidence,
                required_context, risk_flags, tone_guidance, created_at, updated_at
            ) VALUES (
                :source_item_id, :brief_goal, :target_format, :key_claim, :key_evidence,
                :required_context, :risk_flags, :tone_guidance, :created_at, :updated_at
            )
            ON CONFLICT(source_item_id) DO UPDATE SET
                brief_goal = excluded.brief_goal,
                target_format = excluded.target_format,
                key_claim = excluded.key_claim,
                key_evidence = excluded.key_evidence,
                required_context = excluded.required_context,
                risk_flags = excluded.risk_flags,
                tone_guidance = excluded.tone_guidance,
                updated_at = excluded.updated_at
        """, fields)
        return cursor.lastrowid

    def upsert_curation_output(self, output_data: Dict[str, Any]) -> int:
        """
        Upserts a curation output using INSERT ... ON CONFLICT(source_item_id) DO UPDATE.
        """
        now = get_utc_now_iso8601()
        fields = {
            "source_item_id": output_data["source_item_id"],
            "display_title": output_data["display_title"],
            "summary_short": output_data["summary_short"],
            "bullet_1": output_data.get("bullet_1"),
            "bullet_2": output_data.get("bullet_2"),
            "bullet_3": output_data.get("bullet_3"),
            "created_at": output_data.get("created_at", now),
            "updated_at": output_data.get("updated_at", now),
        }

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO curation_output (
                source_item_id, display_title, summary_short, bullet_1, bullet_2,
                bullet_3, created_at, updated_at
            ) VALUES (
                :source_item_id, :display_title, :summary_short, :bullet_1, :bullet_2,
                :bullet_3, :created_at, :updated_at
            )
            ON CONFLICT(source_item_id) DO UPDATE SET
                display_title = excluded.display_title,
                summary_short = excluded.summary_short,
                bullet_1 = excluded.bullet_1,
                bullet_2 = excluded.bullet_2,
                bullet_3 = excluded.bullet_3,
                updated_at = excluded.updated_at
        """, fields)
        return cursor.lastrowid

    def delete_editor_brief(self, source_item_id: int) -> None:
        """
        Deletes the editor brief for a source item if it exists.
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM editor_brief WHERE source_item_id = ?", (source_item_id,))

    def delete_curation_output(self, source_item_id: int) -> None:
        """
        Deletes the curation output for a source item if it exists.
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM curation_output WHERE source_item_id = ?", (source_item_id,))
