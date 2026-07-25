import hashlib
import json
import sqlite3
from typing import Dict, Any, Optional

def get_utc_now_iso8601() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _normalize_line_endings(value: Optional[str]) -> Optional[str]:
    """Convert CRLF and bare CR to LF. NULL passes through unchanged."""
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


def compute_content_fingerprint(
    display_title: str,
    summary_short: str,
    bullet_1: Optional[str] = None,
    bullet_2: Optional[str] = None,
    bullet_3: Optional[str] = None
) -> str:
    """
    Single shared five-field fingerprint helper (DATA_CONTRACT.md section 2.1.1).

    Fixed field order, per-field line-ending normalization, NULL serialized as
    JSON null (never conflated with ""), fixed key order, no-whitespace JSON,
    raw UTF-8 (ensure_ascii=False), SHA-256. UI labels, locale identifiers,
    and site presentation strings never participate in the fingerprint.
    """
    payload = {
        "display_title": _normalize_line_endings(display_title),
        "summary_short": _normalize_line_endings(summary_short),
        "bullet_1": _normalize_line_endings(bullet_1),
        "bullet_2": _normalize_line_endings(bullet_2),
        "bullet_3": _normalize_line_endings(bullet_3),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def assemble_approved_content_records(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Delta-oriented shared handoff assembler.
    Scans curation approvals, copies the five structured content fields
    straight through (never injecting UI presentation labels), computes
    fingerprints, and updates approved_content_record.
    """
    cursor = conn.cursor()
    
    # TODO: Once the 'edit' module is implemented, this query should be updated
    # to also select finalized edit outputs from 'edit_draft' and merge/unify them
    # into the approved_content_record handoff table.
    # Currently, this is a recognized temporary scope limitation since the edit module
    # has not yet been implemented.
    query = """
        SELECT 
            d.source_item_id,
            d.curated_at AS approved_at,
            d.downstream_action,
            o.display_title,
            o.summary_short,
            o.bullet_1,
            o.bullet_2,
            o.bullet_3,
            o.updated_at AS upstream_updated_at
        FROM curation_decision d
        JOIN curation_output o ON d.source_item_id = o.source_item_id
        WHERE d.curate_status = 'approved'
          AND d.downstream_action IN ('publish_link', 'publish_summary')
    """
    cursor.execute(query)
    candidates = cursor.fetchall()

    stats = {
        "scanned": len(candidates),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
    }

    for cand in candidates:
        source_item_id = cand["source_item_id"]
        
        # Load existing handoff record
        cursor.execute("""
            SELECT * FROM approved_content_record WHERE source_item_id = ?
        """, (source_item_id,))
        existing = cursor.fetchone()

        # Delta pre-screen optimization using upstream_updated_at stored in metadata
        existing_upstream_updated_at = None
        if existing and existing["author_metadata"]:
            try:
                meta = json.loads(existing["author_metadata"])
                existing_upstream_updated_at = meta.get("upstream_updated_at")
            except Exception:
                pass

        if existing and existing_upstream_updated_at and cand["upstream_updated_at"] <= existing_upstream_updated_at:
            stats["skipped"] += 1
            continue

        # Straight-through copy of the five content fields
        display_title = cand["display_title"]
        summary_short = cand["summary_short"]
        bullet_1 = cand["bullet_1"]
        bullet_2 = cand["bullet_2"]
        bullet_3 = cand["bullet_3"]

        # Compute fingerprint
        fingerprint = compute_content_fingerprint(
            display_title, summary_short, bullet_1, bullet_2, bullet_3
        )

        # Resolve content language code
        # Under current system policy, all curate-originated mother-drafts are materialized
        # with content_language_code = 'en' (English).
        # We explicitly decouple this from classification_result.primary_language_code,
        # which tracks the original raw source text language.
        content_language_code = 'en'

        now = get_utc_now_iso8601()

        # Author metadata JSON (includes upstream_updated_at to preserve freshness marker)
        author_metadata = json.dumps({
            "source_module": "curate",
            "writer_type": "AI",
            "upstream_updated_at": cand["upstream_updated_at"]
        })

        if not existing:
            # Insert new record (store system time in updated_at to comply with contract)
            cursor.execute("""
                INSERT INTO approved_content_record (
                    source_item_id, display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    content_fingerprint, content_language_code, approved_at, author_metadata,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_item_id, display_title, summary_short, bullet_1, bullet_2, bullet_3,
                fingerprint, content_language_code, cand["approved_at"], author_metadata,
                now, now
            ))
            stats["inserted"] += 1
        else:
            # Re-verify if any value changed
            is_changed = (
                existing["display_title"] != display_title or
                existing["summary_short"] != summary_short or
                existing["bullet_1"] != bullet_1 or
                existing["bullet_2"] != bullet_2 or
                existing["bullet_3"] != bullet_3 or
                existing["content_fingerprint"] != fingerprint or
                existing["content_language_code"] != content_language_code or
                existing["approved_at"] != cand["approved_at"]
            )
            
            if is_changed:
                cursor.execute("""
                    UPDATE approved_content_record
                    SET display_title = ?, summary_short = ?, bullet_1 = ?, bullet_2 = ?, bullet_3 = ?,
                        content_fingerprint = ?, content_language_code = ?, approved_at = ?,
                        author_metadata = ?, updated_at = ?
                    WHERE source_item_id = ?
                """, (
                    display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    fingerprint, content_language_code, cand["approved_at"],
                    author_metadata, now, source_item_id
                ))
                stats["updated"] += 1
            else:
                # Upstream timestamp changed but content is identical: update metadata to prevent re-screen misses
                cursor.execute("""
                    UPDATE approved_content_record
                    SET author_metadata = ?, updated_at = ?
                    WHERE source_item_id = ?
                """, (author_metadata, now, source_item_id))
                stats["skipped"] += 1

    conn.commit()
    return stats
