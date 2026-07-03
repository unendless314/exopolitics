import hashlib
import json
import sqlite3
from typing import Dict, Any, Optional

def get_utc_now_iso8601() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def compute_fingerprint(title: str, body: str) -> str:
    """Normalize line endings and compute SHA-256 fingerprint."""
    norm_title = title.replace("\r\n", "\n").replace("\r", "\n")
    norm_body = body.replace("\r\n", "\n").replace("\r", "\n")
    payload = norm_title + "\n\n" + norm_body
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def splice_content_body(summary_short: str, bullet_1: Optional[str], bullet_2: Optional[str], bullet_3: Optional[str]) -> str:
    """Splice the summary and bullet points into a single markdown body with plain-text English labels."""
    parts = [summary_short]
    bullets_part = []
    if bullet_1:
        bullets_part.append(f"* Key Claim: {bullet_1}")
    if bullet_2:
        bullets_part.append(f"* Evidence Level: {bullet_2}")
    if bullet_3:
        bullets_part.append(f"* Objective Impact: {bullet_3}")
        
    if bullets_part:
        parts.append("\n".join(bullets_part))
        
    return "\n\n".join(parts)

def assemble_approved_content_records(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Delta-oriented shared handoff assembler.
    Scans curation approvals, splices payloads, computes fingerprints,
    and updates approved_content_record.
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

        # Assemble the payload
        display_title = cand["display_title"]
        content_body = splice_content_body(
            cand["summary_short"], cand["bullet_1"], cand["bullet_2"], cand["bullet_3"]
        )

        # Compute fingerprint
        fingerprint = compute_fingerprint(display_title, content_body)

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
                    source_item_id, display_title, content_body, content_fingerprint,
                    content_language_code, approved_at, author_metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_item_id, display_title, content_body, fingerprint,
                content_language_code, cand["approved_at"], author_metadata, now, now
            ))
            stats["inserted"] += 1
        else:
            # Re-verify if any value changed
            is_changed = (
                existing["display_title"] != display_title or
                existing["content_body"] != content_body or
                existing["content_fingerprint"] != fingerprint or
                existing["content_language_code"] != content_language_code or
                existing["approved_at"] != cand["approved_at"]
            )
            
            if is_changed:
                cursor.execute("""
                    UPDATE approved_content_record
                    SET display_title = ?, content_body = ?, content_fingerprint = ?,
                        content_language_code = ?, approved_at = ?, author_metadata = ?,
                        updated_at = ?
                    WHERE source_item_id = ?
                """, (
                    display_title, content_body, fingerprint,
                    content_language_code, cand["approved_at"], author_metadata, now, source_item_id
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
