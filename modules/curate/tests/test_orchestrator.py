import asyncio
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import httpx

from modules.curate.src.config import CurateConfig
from modules.curate.src.database import (
    run_migrations,
    get_connection,
    CurationRepository
)
from modules.curate.src.orchestrator import (
    orchestrate_run,
    validate_curation_response,
    fetch_llm_curation,
    curate_item
)

DEFAULT_CURATE_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

def create_mock_upstream_tables(db_path: pathlib.Path) -> None:
    """Helper to seed the minimal schema required for upstream ingest/classify tables."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                canonical_url TEXT,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested'))
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item_text (
                source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                sanitized_text TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS classification_result (
                classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                topic_class TEXT NOT NULL,
                classification_reason TEXT,
                governmental_involvement INTEGER,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


class TestOrchestrator(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, DEFAULT_CURATE_MIGRATIONS)

        # Mock config setup
        self.config = MagicMock(spec=CurateConfig)
        self.config.active_provider_name = "test-provider"
        self.config.active_provider = MagicMock()
        self.config.active_provider.model_name = "gpt-5.4-mini"
        self.config.active_provider.api_base = "https://api.test.com"
        self.config.active_provider.api_key_env = "TEST_API_KEY"
        self.config.active_provider.supports_structured_output = True

        self.config.active_template = MagicMock()
        self.config.active_template.version = "curator_v1"
        self.config.active_template.system_instruction = "System Instruction"
        self.config.active_template.user_prompt_template = "Title: {raw_title}, Text: {sanitized_text}"

        self.config.execution_policy = MagicMock()
        self.config.execution_policy.batch_size = 20
        self.config.execution_policy.max_concurrent_requests = 3
        self.config.execution_policy.rate_limit_per_minute = 60
        self.config.execution_policy.request_timeout_seconds = 10.0
        self.config.execution_policy.retry_attempts = 2
        self.config.execution_policy.backoff_factor = 0.1

        self.config.request_defaults = MagicMock()
        self.config.request_defaults.temperature = 0.2
        self.config.request_defaults.top_p = 0.95
        self.config.request_defaults.max_output_tokens = 2048

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_upstream_item(self, item_id: int, title: str, text: str, topic_class: str = "core", gov: int = 1) -> None:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO source_item (source_item_id, source_id, title, ingest_status)
                VALUES (?, 1, ?, 'ingested')
            """, (item_id, title))
            cursor.execute("""
                INSERT INTO source_item_text (source_item_id, sanitized_text)
                VALUES (?, ?)
            """, (item_id, text))
            cursor.execute("""
                INSERT INTO classification_result (source_item_id, topic_class, governmental_involvement)
                VALUES (?, ?, ?)
            """, (item_id, topic_class, gov))
            conn.commit()
        finally:
            conn.close()

    def test_validation_matrix_logic(self) -> None:
        # 1. Valid publish_summary
        valid_summary = {
            "curation_decision": {
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "decision_reason": "High quality summary"
            },
            "editor_brief": {
                "brief_goal": "Goal",
                "target_format": "structured_summary",
                "risk_flags": ["clickbait"],
                "tone_guidance": "neutral"
            },
            "curation_output": {
                "display_title": "Title",
                "summary_short": "Summary paragraph",
                "bullet_1": "Claim",
                "bullet_2": "Evidence",
                "bullet_3": "Context"
            }
        }
        validate_curation_response(valid_summary) # should not raise

        # 2. Invalid publish_summary (missing bullet_3)
        invalid_summary = json.loads(json.dumps(valid_summary))
        invalid_summary["curation_output"]["bullet_3"] = None
        with self.assertRaises(ValueError):
            validate_curation_response(invalid_summary)

        # 3. Valid publish_link
        valid_link = {
            "curation_decision": {
                "curate_status": "approved",
                "downstream_action": "publish_link",
                "decision_reason": "Conference link"
            },
            "editor_brief": {
                "brief_goal": "Goal",
                "target_format": "link_card",
                "risk_flags": [],
                "tone_guidance": "neutral"
            },
            "curation_output": {
                "display_title": "Title",
                "summary_short": "Short excerpt"
            }
        }
        validate_curation_response(valid_link) # should not raise

        # 4. Invalid publish_link (bullets present)
        invalid_link = json.loads(json.dumps(valid_link))
        invalid_link["curation_output"]["bullet_1"] = "Not null"
        with self.assertRaises(ValueError):
            validate_curation_response(invalid_link)

        # 5. Valid edit_rewrite
        valid_rewrite = {
            "curation_decision": {
                "curate_status": "rejected",
                "downstream_action": "edit_rewrite",
                "decision_reason": "Needs rewrite"
            },
            "editor_brief": {
                "brief_goal": "Rewrite goal",
                "target_format": "structured_summary",
                "risk_flags": ["unverified"],
                "tone_guidance": "calm"
            },
            "curation_output": None
        }
        validate_curation_response(valid_rewrite) # should not raise

        # 6. Invalid edit_rewrite (curation_output not null)
        invalid_rewrite = json.loads(json.dumps(valid_rewrite))
        invalid_rewrite["curation_output"] = {"display_title": "Title", "summary_short": "Summary"}
        with self.assertRaises(ValueError):
            validate_curation_response(invalid_rewrite)

        # 7. Valid reject_discard
        valid_discard = {
            "curation_decision": {
                "curate_status": "rejected",
                "downstream_action": "reject_discard",
                "decision_reason": "Clickbait"
            },
            "editor_brief": None,
            "curation_output": None
        }
        validate_curation_response(valid_discard) # should not raise

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_publish_summary_success(self, mock_post) -> None:
        self.seed_upstream_item(10, "UFO Alert", "Important congressional disclosure.")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "curation_decision": {
                            "curate_status": "approved",
                            "downstream_action": "publish_summary",
                            "decision_reason": "high_evidence_report"
                        },
                        "editor_brief": {
                            "brief_goal": "Validate base facts",
                            "target_format": "structured_summary",
                            "key_claim": "Spacecraft recovery",
                            "key_evidence": "Official memo",
                            "required_context": "DOD involvement",
                            "risk_flags": ["sensationalist"],
                            "tone_guidance": "neutral"
                        },
                        "curation_output": {
                            "display_title": "Clean UFO Report",
                            "summary_short": "A summary of DOD memo.",
                            "bullet_1": "Claim of spacecraft recovery.",
                            "bullet_2": "Official DOD memorandum dated 2026.",
                            "bullet_3": "Involvement of DOD and Congress."
                        }
                    })
                }
            }]
        }
        mock_post.return_value = mock_resp

        summary = asyncio.run(orchestrate_run(self.config, self.db_path))
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        # Verify DB writes
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM curation_decision WHERE source_item_id = 10")
            dec = cursor.fetchone()
            self.assertEqual(dec["curate_status"], "approved")
            self.assertEqual(dec["downstream_action"], "publish_summary")
            self.assertEqual(dec["retry_count"], 0)

            cursor.execute("SELECT * FROM editor_brief WHERE source_item_id = 10")
            brief = cursor.fetchone()
            self.assertEqual(brief["target_format"], "structured_summary")
            self.assertEqual(json.loads(brief["risk_flags"]), ["sensationalist"])

            cursor.execute("SELECT * FROM curation_output WHERE source_item_id = 10")
            output = cursor.fetchone()
            self.assertEqual(output["display_title"], "Clean UFO Report")
            self.assertEqual(output["bullet_1"], "Claim of spacecraft recovery.")
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_transient_failure_and_retry(self, mock_post) -> None:
        self.seed_upstream_item(20, "UFO Video", "Leaked video from cockpit.")

        mock_fail = MagicMock()
        mock_fail.status_code = 503  # Service unavailable
        mock_post.return_value = mock_fail

        summary = asyncio.run(orchestrate_run(self.config, self.db_path))
        self.assertEqual(summary["processed_successfully"], 0)
        self.assertEqual(summary["failures"], 1)

        # Check failed status write
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM curation_decision WHERE source_item_id = 20")
            dec = cursor.fetchone()
            self.assertEqual(dec["curate_status"], "failed")
            self.assertIsNone(dec["downstream_action"])
            self.assertEqual(dec["retry_count"], 1)
            self.assertIn("503", dec["decision_reason"])
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_locked_item_excluded_from_pending(self, mock_post) -> None:
        # Seed two items: one pending (no decision), one locked (failed 3 times)
        self.seed_upstream_item(30, "Locked item", "Body")
        self.seed_upstream_item(40, "Pending item", "Body")

        conn = get_connection(self.db_path)
        try:
            repo = CurationRepository(conn)
            # Write locked decision manually
            repo.upsert_curation_decision({
                "source_item_id": 30,
                "curate_status": "failed",
                "downstream_action": None,
                "retry_count": 3,
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            conn.commit()
        finally:
            conn.close()

        # Mock LLM API response (success for item 40)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "curation_decision": {
                            "curate_status": "rejected",
                            "downstream_action": "reject_discard",
                            "decision_reason": "opinionated"
                        },
                        "editor_brief": None,
                        "curation_output": None
                    })
                }
            }]
        }
        mock_post.return_value = mock_resp

        # Run batch size 2
        summary = asyncio.run(orchestrate_run(self.config, self.db_path, batch_size=2))
        # It should only query item 40, item 30 is locked out!
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_state_transitions_data_cleanups(self, mock_post) -> None:
        self.seed_upstream_item(50, "Curation Transition Test", "Body text")

        conn = get_connection(self.db_path)
        repo = CurationRepository(conn)
        try:
            # 1. Start state: Approved publish_summary (contains brief & output)
            repo.upsert_curation_decision({
                "source_item_id": 50,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            repo.upsert_editor_brief({
                "source_item_id": 50,
                "brief_goal": "Goal",
                "target_format": "structured_summary",
                "risk_flags": [],
                "tone_guidance": "neutral"
            })
            repo.upsert_curation_output({
                "source_item_id": 50,
                "display_title": "Title",
                "summary_short": "Summary"
            })
            conn.commit()

            # Verify rows exist
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 50")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 50")
            self.assertIsNotNone(cursor.fetchone())

            # 2. Forced Re-run returns "edit_rewrite" (keeps brief, deletes output)
            # Setup mock response
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "curation_decision": {
                                "curate_status": "rejected",
                                "downstream_action": "edit_rewrite",
                                "decision_reason": "needs_translation"
                            },
                            "editor_brief": {
                                "brief_goal": "Translate now",
                                "target_format": "structured_summary",
                                "risk_flags": ["foreign_lang"],
                                "tone_guidance": "neutral"
                            },
                            "curation_output": None
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp

            # Force re-run curation for item 50 manually via curate_item
            db_lock = asyncio.Lock()
            # Fetch the row
            cursor.execute("""
                SELECT s.source_item_id, s.title AS raw_title, s.canonical_url,
                       t.sanitized_text, c.topic_class, c.governmental_involvement
                FROM source_item s
                JOIN source_item_text t ON s.source_item_id = t.source_item_id
                JOIN classification_result c ON s.source_item_id = c.source_item_id
                WHERE s.source_item_id = 50
            """)
            item_row = cursor.fetchone()

            async def run_curate():
                async with httpx.AsyncClient() as client:
                    return await curate_item(
                        repo=repo,
                        client=client,
                        config=self.config,
                        item=item_row,
                        api_key="dummy",
                        db_lock=db_lock,
                        commit=True
                    )
            success = asyncio.run(run_curate())
            self.assertTrue(success)

            # Check database: decision is edit_rewrite, brief exists (updated), output DELETED
            cursor.execute("SELECT downstream_action FROM curation_decision WHERE source_item_id = 50")
            self.assertEqual(cursor.fetchone()["downstream_action"], "edit_rewrite")

            cursor.execute("SELECT brief_goal FROM editor_brief WHERE source_item_id = 50")
            self.assertEqual(cursor.fetchone()["brief_goal"], "Translate now")

            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 50")
            self.assertIsNone(cursor.fetchone())

            # 3. Transition to reject_discard (deletes both brief and output)
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "curation_decision": {
                                "curate_status": "rejected",
                                "downstream_action": "reject_discard",
                                "decision_reason": "duplicate"
                            },
                            "editor_brief": None,
                            "curation_output": None
                        })
                    }
                }]
            }

            async def run_curate_2():
                async with httpx.AsyncClient() as client:
                    return await curate_item(
                        repo=repo,
                        client=client,
                        config=self.config,
                        item=item_row,
                        api_key="dummy",
                        db_lock=db_lock,
                        commit=True
                    )
            success2 = asyncio.run(run_curate_2())
            self.assertTrue(success2)

            # Check database: decision is reject_discard, brief DELETED, output DELETED
            cursor.execute("SELECT downstream_action FROM curation_decision WHERE source_item_id = 50")
            self.assertEqual(cursor.fetchone()["downstream_action"], "reject_discard")

            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 50")
            self.assertIsNone(cursor.fetchone())

            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 50")
            self.assertIsNone(cursor.fetchone())

        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_forced_rerun_failure_rollback(self, mock_post) -> None:
        self.seed_upstream_item(60, "Rollback Test", "Some text")

        conn = get_connection(self.db_path)
        repo = CurationRepository(conn)
        try:
            # Seed starting completed state: Approved publish_summary
            repo.upsert_curation_decision({
                "source_item_id": 60,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            repo.upsert_editor_brief({
                "source_item_id": 60,
                "brief_goal": "Goal",
                "target_format": "structured_summary",
                "risk_flags": [],
                "tone_guidance": "neutral"
            })
            repo.upsert_curation_output({
                "source_item_id": 60,
                "display_title": "Old Title",
                "summary_short": "Old Summary"
            })
            conn.commit()

            # Mock LLM API throws exception (e.g. rate limit error 429)
            mock_fail = MagicMock()
            mock_fail.status_code = 429
            mock_post.return_value = mock_fail

            db_lock = asyncio.Lock()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.source_item_id, s.title AS raw_title, s.canonical_url,
                       t.sanitized_text, c.topic_class, c.governmental_involvement
                FROM source_item s
                JOIN source_item_text t ON s.source_item_id = t.source_item_id
                JOIN classification_result c ON s.source_item_id = c.source_item_id
                WHERE s.source_item_id = 60
            """)
            item_row = cursor.fetchone()

            async def run_curate_fail():
                async with httpx.AsyncClient() as client:
                    return await curate_item(
                        repo=repo,
                        client=client,
                        config=self.config,
                        item=item_row,
                        api_key="dummy",
                        db_lock=db_lock,
                        commit=True
                    )
            # Run curate_item
            success = asyncio.run(run_curate_fail())
            # curate_item should return False on failure
            self.assertFalse(success)

            # Check database: state must be COMPLETELY UNCHANGED
            cursor.execute("SELECT curate_status, downstream_action, retry_count FROM curation_decision WHERE source_item_id = 60")
            dec = cursor.fetchone()
            self.assertEqual(dec["curate_status"], "approved")
            self.assertEqual(dec["downstream_action"], "publish_summary")
            self.assertEqual(dec["retry_count"], 0)

            cursor.execute("SELECT display_title FROM curation_output WHERE source_item_id = 60")
            self.assertEqual(cursor.fetchone()["display_title"], "Old Title")

        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_with_source_item_id_and_force(self, mock_post) -> None:
        # Seed item 70 (completed: approved)
        self.seed_upstream_item(70, "Target Item", "Sanitized content")
        
        conn = get_connection(self.db_path)
        repo = CurationRepository(conn)
        try:
            repo.upsert_curation_decision({
                "source_item_id": 70,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            conn.commit()
            
            # 1. Running without force on a completed item must raise ValueError
            with self.assertRaises(ValueError):
                asyncio.run(orchestrate_run(
                    config=self.config,
                    db_path=self.db_path,
                    source_item_id=70,
                    force=False
                ))
                
            # 2. Running with force should succeed
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "curation_decision": {
                                "curate_status": "rejected",
                                "downstream_action": "reject_discard",
                                "decision_reason": "duplicate"
                            },
                            "editor_brief": None,
                            "curation_output": None
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp
            
            summary = asyncio.run(orchestrate_run(
                config=self.config,
                db_path=self.db_path,
                source_item_id=70,
                force=True
            ))
            self.assertEqual(summary["processed_successfully"], 1)
            
            # Verify DB state updated to reject_discard
            cursor = conn.cursor()
            cursor.execute("SELECT downstream_action FROM curation_decision WHERE source_item_id = 70")
            self.assertEqual(cursor.fetchone()["downstream_action"], "reject_discard")

            # 3. Seed item 80 (completed: failed, retry_count=3, i.e. locked)
            self.seed_upstream_item(80, "Locked Item", "Sanitized content")
            repo.upsert_curation_decision({
                "source_item_id": 80,
                "curate_status": "failed",
                "downstream_action": None,
                "retry_count": 3,
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            conn.commit()

            # 4. Running without force on a locked item must raise ValueError
            with self.assertRaises(ValueError):
                asyncio.run(orchestrate_run(
                    config=self.config,
                    db_path=self.db_path,
                    source_item_id=80,
                    force=False
                ))

            # 5. Running with force should succeed
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "curation_decision": {
                                "curate_status": "approved",
                                "downstream_action": "publish_link",
                                "decision_reason": "override_success"
                            },
                            "editor_brief": {
                                "brief_goal": "Goal",
                                "target_format": "link_card",
                                "risk_flags": [],
                                "tone_guidance": "neutral"
                            },
                            "curation_output": {
                                "display_title": "Clean Title",
                                "summary_short": "Excerpt"
                            }
                        })
                    }
                }]
            }

            summary = asyncio.run(orchestrate_run(
                config=self.config,
                db_path=self.db_path,
                source_item_id=80,
                force=True
            ))
            self.assertEqual(summary["processed_successfully"], 1)

            # Verify DB state updated to approved and retry_count reset to 0
            cursor.execute("SELECT curate_status, downstream_action, retry_count FROM curation_decision WHERE source_item_id = 80")
            dec = cursor.fetchone()
            self.assertEqual(dec["curate_status"], "approved")
            self.assertEqual(dec["downstream_action"], "publish_link")
            self.assertEqual(dec["retry_count"], 0)
            
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_withdrawn_transitions(self, mock_post) -> None:
        # Seed source item 90
        self.seed_upstream_item(90, "Withdrawn Test Item", "Sanitized content")

        conn = get_connection(self.db_path)
        repo = CurationRepository(conn)
        try:
            # 1. Write initial withdrawn state manually
            repo.upsert_curation_decision({
                "source_item_id": 90,
                "curate_status": "withdrawn",
                "downstream_action": "publish_summary",
                "decision_actor": "operator",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "curator_v1"
            })
            conn.commit()

            # 2. Running without force on a withdrawn item must raise ValueError
            with self.assertRaises(ValueError):
                asyncio.run(orchestrate_run(
                    config=self.config,
                    db_path=self.db_path,
                    source_item_id=90,
                    force=False
                ))

            # 3. Forced re-run failure: rollback without changes or retry increment
            mock_fail = MagicMock()
            mock_fail.status_code = 429
            mock_post.return_value = mock_fail

            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.source_item_id, s.title AS raw_title, s.canonical_url,
                       t.sanitized_text, c.topic_class, c.governmental_involvement
                FROM source_item s
                JOIN source_item_text t ON s.source_item_id = t.source_item_id
                JOIN classification_result c ON s.source_item_id = c.source_item_id
                WHERE s.source_item_id = 90
            """)
            item_row = cursor.fetchone()

            db_lock = asyncio.Lock()
            async def run_curate_fail():
                async with httpx.AsyncClient() as client:
                    return await curate_item(
                        repo=repo,
                        client=client,
                        config=self.config,
                        item=item_row,
                        api_key="dummy",
                        db_lock=db_lock,
                        commit=True
                    )
            success_fail = asyncio.run(run_curate_fail())
            self.assertFalse(success_fail)

            # Check database: state must be completely unchanged (still withdrawn)
            cursor.execute("SELECT curate_status, downstream_action, retry_count FROM curation_decision WHERE source_item_id = 90")
            dec_fail = cursor.fetchone()
            self.assertEqual(dec_fail["curate_status"], "withdrawn")
            self.assertEqual(dec_fail["downstream_action"], "publish_summary")
            self.assertEqual(dec_fail["retry_count"], 0)

            # 4. Forced re-run success: turns into approved or rejected
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "curation_decision": {
                                "curate_status": "rejected",
                                "downstream_action": "reject_discard",
                                "decision_reason": "not_relevant"
                            },
                            "editor_brief": None,
                            "curation_output": None
                        })
                    }
                }]
            }
            mock_post.return_value = mock_resp

            summary = asyncio.run(orchestrate_run(
                config=self.config,
                db_path=self.db_path,
                source_item_id=90,
                force=True
            ))
            self.assertEqual(summary["processed_successfully"], 1)

            # Verify database updated to rejected
            cursor.execute("SELECT curate_status, downstream_action, decision_actor FROM curation_decision WHERE source_item_id = 90")
            dec_success = cursor.fetchone()
            self.assertEqual(dec_success["curate_status"], "rejected")
            self.assertEqual(dec_success["downstream_action"], "reject_discard")
            self.assertEqual(dec_success["decision_actor"], "system")

        finally:
            conn.close()

