import json
import os
import pathlib
import shutil
import sqlite3
import re
from typing import Dict, Any, List, Set, Tuple, Optional

from .config import PublishConfig
from .database import PublishRepository, get_connection, transaction, get_utc_now_iso8601

class ValidationError(Exception):
    """Custom exception raised when artifact validation fails."""
    pass

def slugify(text: str) -> str:
    """
    Generate a URL-safe, lowercase slug from a string.
    """
    import unicodedata
    # Normalize unicode to ASCII representation
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    text = text.lower()
    # Replace non-alphanumeric character sequences with hyphens
    text = re.sub(r'[^a-z0-9\-]+', '-', text)
    # Collapse consecutive hyphens
    text = re.sub(r'-+', '-', text)
    # Strip leading and trailing hyphens
    text = text.strip('-')
    return text

def generate_slug(title: str, existing_slugs: Set[str]) -> str:
    """
    Generate a unique slug deterministically by appending a counter suffix on collision.
    """
    base_slug = slugify(title)
    if not base_slug:
        base_slug = "item"
    
    slug = base_slug
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug

def extract_summary_short(content: str, limit: int = 300) -> str:
    """
    Derive a short preview summary from the first paragraph of markdown content.
    """
    if not content:
        return ""
    # Split content into paragraphs
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    
    # Find the first paragraph that is not a heading
    first_p = ""
    for p in paragraphs:
        if not p.startswith("#"):
            first_p = p
            break
    if not first_p:
        first_p = paragraphs[0] if paragraphs else ""
        
    # Strip basic markdown link formatting and collapse spaces
    text = first_p
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*\*|__|\*|_', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text

def validate_item_payload(payload: Dict[str, Any]) -> None:
    """
    Validates that a language artifact conforms to the publish data contract.
    Aborts execution by raising ValidationError if any rule is violated.
    """
    display_title = payload.get("display_title")
    content = payload.get("content")
    language_code = payload.get("language_code")
    slug = payload.get("slug")
    author_metadata_str = payload.get("author_metadata")

    if not display_title or not display_title.strip():
        raise ValidationError("display_title must be non-empty")
    if not content or not content.strip():
        raise ValidationError("content must be non-empty")
    if not language_code or not language_code.strip():
        raise ValidationError("language_code must be present")
    if not slug or not slug.strip():
        raise ValidationError("slug must be present")

    # Author metadata validation
    if author_metadata_str is None:
        raise ValidationError("author_metadata is required and cannot be NULL")

    try:
        author_metadata = json.loads(author_metadata_str)
    except Exception as e:
        raise ValidationError(f"author_metadata is invalid JSON: {str(e)}")

    if not isinstance(author_metadata, dict):
        raise ValidationError("author_metadata must parse to a JSON object")

    if "source_module" not in author_metadata:
        raise ValidationError("author_metadata is missing required key: 'source_module'")
    if "writer_type" not in author_metadata:
        raise ValidationError("author_metadata is missing required key: 'writer_type'")

    writer_type = author_metadata.get("writer_type")
    if writer_type in ("human", "hybrid"):
        editor = author_metadata.get("editor")
        if not editor or not str(editor).strip():
            raise ValidationError(f"editor field is required and must be non-empty when writer_type is '{writer_type}'")
    elif writer_type not in ("AI", "machine"):
        raise ValidationError(f"invalid writer_type: '{writer_type}'")

def get_disclosure_note(author_metadata_str: str) -> str:
    """
    Get the disclosure note based on writer_type.
    Assumes author_metadata_str is already validated.
    """
    author_metadata = json.loads(author_metadata_str)
    writer_type = author_metadata.get("writer_type")
    if writer_type in ("human", "hybrid"):
        return "This item is AI-assisted and human-curated."
    else:
        return "This item is AI-generated."
async def orchestrate_run(
    config: PublishConfig,
    db_path: pathlib.Path,
    export_dir: pathlib.Path,
    rebuild: bool = False
) -> Dict[str, Any]:
    """
    Orchestrate incremental run or full rebuild.
    """
    # Initialize connection
    conn = get_connection(db_path)
    try:
        repo = PublishRepository(conn)
        
        # 1. Target Language Existence Validation (Section 7.1)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translation_output'")
        if not cursor.fetchone():
            raise RuntimeError("Database tables do not exist yet. Run migrate first.")
            
        cursor.execute("SELECT DISTINCT language_code FROM translation_output WHERE translation_status = 'completed'")
        completed_languages = {row[0] for row in cursor.fetchall()}
        
        emitted_warnings = set()
        for lang in config.target_languages:
            if lang not in completed_languages:
                if lang not in emitted_warnings:
                    print(f"Warning: Target language '{lang}' has zero completed translations in the database.")
                    emitted_warnings.add(lang)

        # 2. Reconciliation Candidate Selection
        candidates = repo.get_reconciliation_candidates()
        
        # Group candidates by source_item_id
        candidates_by_item: Dict[int, Dict[str, sqlite3.Row]] = {}
        for row in candidates:
            item_id = row["source_item_id"]
            if item_id not in candidates_by_item:
                candidates_by_item[item_id] = {}
            candidates_by_item[item_id][row["language_code"]] = row

        # Apply coverage policy (strict_match)
        configured_langs = set(config.target_languages.keys())
        eligible_source_item_ids = set()
        
        for item_id, lang_map in candidates_by_item.items():
            # For strict match, all configured target languages must be present
            has_all_languages = True
            for lang in configured_langs:
                if lang not in lang_map:
                    has_all_languages = False
                    break
            if has_all_languages:
                eligible_source_item_ids.add(item_id)

        # Build set of eligible (item_id, language_code) pairs
        eligible_pairs = set()
        for item_id in eligible_source_item_ids:
            for lang in configured_langs:
                eligible_pairs.add((item_id, lang))

        # Query active publish statuses
        active_statuses = repo.get_active_publish_statuses()
        currently_published_pairs = {}
        for row in active_statuses:
            if row["publish_status"] == 'published':
                currently_published_pairs[(row["source_item_id"], row["language_code"])] = row

        # Identify items to publish or update
        items_to_publish_or_update: List[Tuple[int, str, str]] = []  # (source_item_id, language_code, content_fingerprint)
        for (item_id, lang) in eligible_pairs:
            candidate_row = candidates_by_item[item_id][lang]
            fingerprint = candidate_row["content_fingerprint"]
            
            pub_row = currently_published_pairs.get((item_id, lang))
            if not pub_row:
                items_to_publish_or_update.append((item_id, lang, fingerprint))
            elif pub_row["source_fingerprint"] != fingerprint:
                items_to_publish_or_update.append((item_id, lang, fingerprint))

        # Identify items to withdraw
        items_to_withdraw: List[Tuple[int, str, str, str]] = []  # (source_item_id, language_code, slug, fingerprint)
        for (item_id, lang), pub_row in currently_published_pairs.items():
            if (item_id, lang) not in eligible_pairs:
                items_to_withdraw.append((item_id, lang, pub_row["slug"], pub_row["source_fingerprint"]))

        # 3. Slug Assignment & DB updates
        existing_slugs = repo.get_all_frozen_slugs()
        
        # Create map of existing status rows to track prior states (Issue 2 compensation fix)
        existing_status_rows = {(row["source_item_id"], row["language_code"]): row for row in active_statuses}

        # We track which items and languages were mutated in this run
        mutated_pairs: Set[Tuple[int, str]] = set()
        
        # Set up export directory
        export_dir.mkdir(parents=True, exist_ok=True)
        
        published_count = 0
        withdrawn_count = 0

        # --- A. Reconciliation Phase (Database State Sync) ---
        # First, update the database status for publications/updates (with in-memory validation first)
        for item_id, lang, fingerprint in items_to_publish_or_update:
            # 1. Fetch or generate slug in memory first and capture initial state
            pub_rec = repo.get_publish_record_by_source_item_id(item_id)
            had_pub_rec = pub_rec is not None
            prior_lang_status = repo.get_publish_language_status(pub_rec["publish_record_id"], lang) if pub_rec else None
            had_lang_status = prior_lang_status is not None
            if not pub_rec:
                title_src = ""
                lang_map = candidates_by_item[item_id]
                if "en" in lang_map:
                    title_src = lang_map["en"]["display_title"]
                else:
                    for r in lang_map.values():
                        if r["display_title"]:
                            title_src = r["display_title"]
                            break
                slug = generate_slug(title_src, existing_slugs)
                # Keep in existing_slugs to avoid collision within this batch
                existing_slugs.add(slug)
            else:
                slug = pub_rec["slug"]

            # 2. Validate canonical payload before mutating database
            payload_row = repo.fetch_canonical_item_payload(item_id, lang)
            if not payload_row:
                raise ValidationError(f"Canonical data missing for item {item_id} lang {lang}")
            
            payload = dict(payload_row)
            payload["slug"] = slug  # Populate slug for validation
            validate_item_payload(payload)

            # 3. DB Transaction: Update metadata & status
            with transaction(conn, commit=True):
                # Re-query inside transaction to ensure concurrency/idempotency safety
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                if not pub_rec:
                    # We reuse the generated slug from above
                    first_pub_time = get_utc_now_iso8601()
                    publish_record_id = repo.insert_publish_record(item_id, slug, first_pub_time)
                else:
                    publish_record_id = pub_rec["publish_record_id"]
                    slug = pub_rec["slug"]
                    repo.update_publish_record_updated_at(publish_record_id, get_utc_now_iso8601())

                repo.upsert_publish_language_status(
                    publish_record_id=publish_record_id,
                    language_code=lang,
                    publish_status='published',
                    published_at=get_utc_now_iso8601(),
                    withdrawn_at=None,
                    source_fingerprint=fingerprint
                )

            # If incremental, emit the file immediately
            if not rebuild:
                author_metadata = json.loads(payload["author_metadata"])
                disclosure_note = get_disclosure_note(payload["author_metadata"])

                item_json = {
                    "source_item_id": payload["source_item_id"],
                    "language_code": payload["language_code"],
                    "slug": slug,
                    "display_title": payload["display_title"],
                    "content": payload["content"],
                    "canonical_url": payload["canonical_url"],
                    "source_published_at": payload["source_published_at"],
                    "approved_at": payload["approved_at"],
                    "published_at": get_utc_now_iso8601(),
                    "downstream_action": payload["downstream_action"],
                    "disclosure_note": disclosure_note,
                    "author_metadata": author_metadata
                }

                item_file_dir = export_dir / lang / "items"
                item_file_dir.mkdir(parents=True, exist_ok=True)
                item_file_path = item_file_dir / f"{slug}.json"
                try:
                    with open(item_file_path, "w", encoding="utf-8") as f:
                        json.dump(item_json, f, indent=2, ensure_ascii=False)
                except Exception as write_err:
                    # Compensating transaction to restore database state (Issue 2 fix)
                    with transaction(conn, commit=True):
                        if had_lang_status:
                            # Restore previous status
                            prev_status = prior_lang_status
                            repo.upsert_publish_language_status(
                                publish_record_id=publish_record_id,
                                language_code=lang,
                                publish_status=prev_status["publish_status"],
                                published_at=prev_status["published_at"],
                                withdrawn_at=prev_status["withdrawn_at"],
                                source_fingerprint=prev_status["source_fingerprint"]
                            )
                        else:
                            # First time publish failure: delete the new status row to restore pending state
                            cursor = conn.cursor()
                            cursor.execute(
                                "DELETE FROM publish_language_status WHERE publish_record_id = ? AND language_code = ?",
                                (publish_record_id, lang)
                            )
                            if not had_pub_rec:
                                # First time publish record: delete the parent record too
                                cursor.execute(
                                    "DELETE FROM publish_record WHERE publish_record_id = ?",
                                    (publish_record_id,)
                                )
                    raise write_err

                mutated_pairs.add((item_id, lang))
                published_count += 1

        # Second, update database status for withdrawals
        for item_id, lang, slug, fingerprint in items_to_withdraw:
            with transaction(conn, commit=True):
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                if pub_rec:
                    repo.upsert_publish_language_status(
                        publish_record_id=pub_rec["publish_record_id"],
                        language_code=lang,
                        publish_status='withdrawn',
                        published_at=None,
                        withdrawn_at=get_utc_now_iso8601(),
                        source_fingerprint=fingerprint # Preserve previously published fingerprint!
                    )
                    repo.update_publish_record_updated_at(pub_rec["publish_record_id"], get_utc_now_iso8601())

            # If incremental, delete the file immediately
            if not rebuild:
                item_file_path = export_dir / lang / "items" / f"{slug}.json"
                if item_file_path.exists():
                    os.remove(item_file_path)
                mutated_pairs.add((item_id, lang))
                withdrawn_count += 1

        # --- B. File Emission Phase ---
        if rebuild:
            # Clear all target directories
            for lang in config.target_languages:
                lang_items_dir = export_dir / lang / "items"
                if lang_items_dir.exists():
                    shutil.rmtree(lang_items_dir)
                lang_archives_dir = export_dir / lang / "archives"
                if lang_archives_dir.exists():
                    shutil.rmtree(lang_archives_dir)
                lang_index_file = export_dir / lang / "index.json"
                if lang_index_file.exists():
                    os.remove(lang_index_file)

            # Re-fetch all published status records from DB to rebuild all eligible files
            cursor.execute("""
                SELECT pr.source_item_id, pls.language_code, pr.slug, pls.published_at
                FROM publish_record pr
                JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                WHERE pls.publish_status = 'published'
            """)
            published_rows = cursor.fetchall()

            for row in published_rows:
                item_id, lang, slug, published_at = row["source_item_id"], row["language_code"], row["slug"], row["published_at"]
                
                payload_row = repo.fetch_canonical_item_payload(item_id, lang)
                if not payload_row:
                    raise ValidationError(f"Canonical data missing for published item {item_id} lang {lang}")

                payload = dict(payload_row)
                payload["slug"] = slug  # Populate slug for validation
                validate_item_payload(payload)

                author_metadata = json.loads(payload["author_metadata"])
                disclosure_note = get_disclosure_note(payload["author_metadata"])

                item_json = {
                    "source_item_id": payload["source_item_id"],
                    "language_code": payload["language_code"],
                    "slug": slug,
                    "display_title": payload["display_title"],
                    "content": payload["content"],
                    "canonical_url": payload["canonical_url"],
                    "source_published_at": payload["source_published_at"],
                    "approved_at": payload["approved_at"],
                    "published_at": published_at,
                    "downstream_action": payload["downstream_action"],
                    "disclosure_note": disclosure_note,
                    "author_metadata": author_metadata
                }

                item_file_dir = export_dir / lang / "items"
                item_file_dir.mkdir(parents=True, exist_ok=True)
                item_file_path = item_file_dir / f"{slug}.json"
                with open(item_file_path, "w", encoding="utf-8") as f:
                    json.dump(item_json, f, indent=2, ensure_ascii=False)

                published_count += 1
                mutated_pairs.add((item_id, lang))

            # Withdrawn count is simply the count of items we withdrew in the DB phase
            withdrawn_count = len(items_to_withdraw)


        # 4. Rebuilding Aggregate Files
        # Compute affected months
        # For rebuild, we affect all months with published items.
        # For incremental, we only affect months of mutated items.
        affected_months_by_lang: Dict[str, Set[str]] = {lang: set() for lang in config.target_languages}
        
        if rebuild:
            # Find all months for active published items in DB
            cursor = conn.cursor()
            for lang in config.target_languages:
                cursor.execute("""
                    SELECT DISTINCT SUBSTR(s.published_at, 1, 7)
                    FROM publish_record pr
                    JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                    JOIN source_item s ON s.source_item_id = pr.source_item_id
                    WHERE pls.language_code = ? AND pls.publish_status = 'published'
                """, (lang,))
                affected_months_by_lang[lang] = {row[0] for row in cursor.fetchall() if row[0]}
        else:
            # Find months for mutated items
            cursor = conn.cursor()
            for item_id, lang in mutated_pairs:
                cursor.execute("SELECT published_at FROM source_item WHERE source_item_id = ?", (item_id,))
                res = cursor.fetchone()
                if res and res[0]:
                    month = res[0][:7]  # YYYY-MM
                    affected_months_by_lang[lang].add(month)

        batch_size = config.execution_policy.batch_size
        latest_limit = config.index_policy.latest_limit

        for lang in config.target_languages:
            # --- 4.1 Rebuild Latest Index.json ---
            index_items = []
            offset = 0
            while True:
                # Query in batches to respect Section 9.3 memory scalability
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        pr.slug,
                        t.display_title,
                        t.content,
                        s.canonical_url,
                        s.published_at AS source_published_at,
                        a.approved_at,
                        pls.published_at
                    FROM publish_record pr
                    JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                    JOIN approved_content_record a ON a.source_item_id = pr.source_item_id
                    JOIN translation_output t ON t.parent_content_id = a.parent_content_id AND t.source_fingerprint = a.content_fingerprint AND t.language_code = pls.language_code
                    JOIN source_item s ON s.source_item_id = pr.source_item_id
                    WHERE pls.language_code = ? AND pls.publish_status = 'published'
                    ORDER BY source_published_at DESC, pr.slug ASC
                    LIMIT ? OFFSET ?
                """, (lang, batch_size, offset))
                
                rows = cursor.fetchall()
                if not rows:
                    break
                
                for row in rows:
                    summary_short = extract_summary_short(row["content"])
                    index_items.append({
                        "slug": row["slug"],
                        "display_title": row["display_title"],
                        "summary_short": summary_short,
                        "canonical_url": row["canonical_url"],
                        "source_published_at": row["source_published_at"],
                        "approved_at": row["approved_at"],
                        "published_at": row["published_at"]
                    })
                
                offset += batch_size
                if len(index_items) >= latest_limit:
                    index_items = index_items[:latest_limit]
                    break

            lang_dir = export_dir / lang
            lang_dir.mkdir(parents=True, exist_ok=True)
            index_path = lang_dir / "index.json"
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(index_items, f, indent=2, ensure_ascii=False)

            # --- 4.2 Rebuild Affected Monthly Archives ---
            archives_dir = lang_dir / "archives"
            archives_dir.mkdir(parents=True, exist_ok=True)
            
            for month in affected_months_by_lang[lang]:
                archive_items = []
                offset = 0
                while True:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT
                            pr.slug,
                            t.display_title,
                            t.content,
                            s.canonical_url,
                            s.published_at AS source_published_at,
                            a.approved_at,
                            pls.published_at
                        FROM publish_record pr
                        JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                        JOIN approved_content_record a ON a.source_item_id = pr.source_item_id
                        JOIN translation_output t ON t.parent_content_id = a.parent_content_id AND t.source_fingerprint = a.content_fingerprint AND t.language_code = pls.language_code
                        JOIN source_item s ON s.source_item_id = pr.source_item_id
                        WHERE pls.language_code = ? AND pls.publish_status = 'published' AND SUBSTR(s.published_at, 1, 7) = ?
                        ORDER BY source_published_at DESC, pr.slug ASC
                        LIMIT ? OFFSET ?
                    """, (lang, month, batch_size, offset))
                    
                    rows = cursor.fetchall()
                    if not rows:
                        break
                    
                    for row in rows:
                        summary_short = extract_summary_short(row["content"])
                        archive_items.append({
                            "slug": row["slug"],
                            "display_title": row["display_title"],
                            "summary_short": summary_short,
                            "canonical_url": row["canonical_url"],
                            "source_published_at": row["source_published_at"],
                            "approved_at": row["approved_at"],
                            "published_at": row["published_at"]
                        })
                    offset += batch_size

                month_file_name = f"archive_{month.replace('-', '_')}.json"
                archive_file_path = archives_dir / month_file_name
                
                if archive_items:
                    with open(archive_file_path, "w", encoding="utf-8") as f:
                        json.dump(archive_items, f, indent=2, ensure_ascii=False)
                else:
                    # Section 3.7: Delete empty archive file
                    if archive_file_path.exists():
                        os.remove(archive_file_path)

            # --- 4.3 Rebuild Archives Manifest ---
            # Retrieve counts and updated_at directly from DB to avoid scanning historical files
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    SUBSTR(s.published_at, 1, 7) AS archive_month,
                    COUNT(*) AS item_count,
                    MAX(
                        CASE
                            WHEN pls.publish_status = 'published' THEN COALESCE(pls.published_at, pls.created_at)
                            ELSE COALESCE(pls.withdrawn_at, pls.created_at)
                        END
                    ) AS last_updated
                FROM publish_record pr
                JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                JOIN source_item s ON s.source_item_id = pr.source_item_id
                WHERE pls.language_code = ? AND pls.publish_status = 'published'
                GROUP BY archive_month
                ORDER BY archive_month DESC;
            """, (lang,))
            
            manifest_rows = cursor.fetchall()
            manifest_json = []
            for row in manifest_rows:
                m_month = row["archive_month"]
                if not m_month:
                    continue
                manifest_json.append({
                    "archive_month": m_month,
                    "file_name": f"archive_{m_month.replace('-', '_')}.json",
                    "item_count": row["item_count"],
                    "updated_at": row["last_updated"]
                })

            manifest_path = archives_dir / "index.json"
            if manifest_json:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest_json, f, indent=2, ensure_ascii=False)
            else:
                if manifest_path.exists():
                    os.remove(manifest_path)

        # --- 5. Rebuild Global Stats.json ---
        stats_json = {}
        
        # 5.1 total_active_published_items_by_language
        cursor = conn.cursor()
        cursor.execute("SELECT language_code, COUNT(*) FROM publish_language_status WHERE publish_status = 'published' GROUP BY language_code")
        stats_json["total_active_published_items_by_language"] = {row[0]: row[1] for row in cursor.fetchall()}
        # Ensure all configured languages exist in dict
        for lang in config.target_languages:
            if lang not in stats_json["total_active_published_items_by_language"]:
                stats_json["total_active_published_items_by_language"][lang] = 0

        # 5.2 total_withdrawn_items_by_language
        cursor.execute("SELECT language_code, COUNT(*) FROM publish_language_status WHERE publish_status = 'withdrawn' GROUP BY language_code")
        stats_json["total_withdrawn_items_by_language"] = {row[0]: row[1] for row in cursor.fetchall()}
        for lang in config.target_languages:
            if lang not in stats_json["total_withdrawn_items_by_language"]:
                stats_json["total_withdrawn_items_by_language"][lang] = 0

        # 5.3 latest_index_count_by_language
        stats_json["latest_index_count_by_language"] = {}
        for lang in config.target_languages:
            count = stats_json["total_active_published_items_by_language"][lang]
            stats_json["latest_index_count_by_language"][lang] = min(count, latest_limit)

        # 5.4 archive_month_count_by_language
        stats_json["archive_month_count_by_language"] = {}
        # 5.5 oldest_archive_month_by_language
        stats_json["oldest_archive_month_by_language"] = {}
        for lang in config.target_languages:
            cursor.execute("""
                SELECT COUNT(DISTINCT SUBSTR(s.published_at, 1, 7)), MIN(SUBSTR(s.published_at, 1, 7))
                FROM publish_record pr
                JOIN publish_language_status pls ON pls.publish_record_id = pr.publish_record_id
                JOIN source_item s ON s.source_item_id = pr.source_item_id
                WHERE pls.language_code = ? AND pls.publish_status = 'published'
            """, (lang,))
            res = cursor.fetchone()
            stats_json["archive_month_count_by_language"][lang] = res[0] if res else 0
            stats_json["oldest_archive_month_by_language"][lang] = res[1] if (res and res[1]) else None

        stats_json["last_export_run_timestamp"] = get_utc_now_iso8601()

        stats_path = export_dir / "stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_json, f, indent=2, ensure_ascii=False)

        return {
            "status": "success",
            "published_count": published_count,
            "withdrawn_count": withdrawn_count,
            "errors": []
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "status": "failure",
            "published_count": 0,
            "withdrawn_count": 0,
            "errors": [str(e)]
        }
    finally:
        conn.close()
