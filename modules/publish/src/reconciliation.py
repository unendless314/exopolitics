"""
Pure reconciliation diff logic for the publish run.

Extracted from orchestrator.py as part of the Phase A surviving-code split
(known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md): pure move,
zero behavior change. Given the reconciliation candidates and the active
publish statuses, decide which (source_item_id, language_code) pairs must be
published/updated and which must be withdrawn. No clock, database or
filesystem access happens here; the queries that produce the inputs stay in
the repository and the mutation phase stays in the orchestrator.
"""
import sqlite3
from typing import Dict, List, NamedTuple, Set, Tuple


class ReconciliationDiff(NamedTuple):
    # Candidates grouped by source_item_id, then language_code; reused
    # downstream for slug title lookup during the mutation phase.
    candidates_by_item: Dict[int, Dict[str, sqlite3.Row]]
    # (source_item_id, language_code, content_fingerprint)
    items_to_publish_or_update: List[Tuple[int, str, str]]
    # (source_item_id, language_code, slug, source_fingerprint)
    items_to_withdraw: List[Tuple[int, str, str, str]]


def compute_reconciliation_diff(
    candidates: List[sqlite3.Row],
    active_statuses: List[sqlite3.Row],
    configured_languages: Set[str],
) -> ReconciliationDiff:
    """
    Diff the reconciliation candidates against the active publish statuses
    under the strict-match coverage policy: every configured target language
    must have a completed, fingerprint-matching translation for the item to
    be eligible.
    """
    # Group candidates by source_item_id
    candidates_by_item: Dict[int, Dict[str, sqlite3.Row]] = {}
    for row in candidates:
        item_id = row["source_item_id"]
        if item_id not in candidates_by_item:
            candidates_by_item[item_id] = {}
        candidates_by_item[item_id][row["language_code"]] = row

    # Apply coverage policy (strict_match)
    eligible_source_item_ids = set()

    for item_id, lang_map in candidates_by_item.items():
        # For strict match, all configured target languages must be present
        has_all_languages = True
        for lang in configured_languages:
            if lang not in lang_map:
                has_all_languages = False
                break
        if has_all_languages:
            eligible_source_item_ids.add(item_id)

    # Build set of eligible (item_id, language_code) pairs
    eligible_pairs = set()
    for item_id in eligible_source_item_ids:
        for lang in configured_languages:
            eligible_pairs.add((item_id, lang))

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

    return ReconciliationDiff(
        candidates_by_item=candidates_by_item,
        items_to_publish_or_update=items_to_publish_or_update,
        items_to_withdraw=items_to_withdraw,
    )
