import asyncio
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import httpx

from modules.translate.src.config import TranslateConfig
from modules.translate.src.database import (
    run_migrations,
    get_connection,
    TranslationRepository,
)
from modules.translate.src.approved_content_record import (
    assemble_approved_content_records,
    compute_content_fingerprint
)
from modules.translate.src.orchestrator import (
    validate_translation_response,
    translate_task,
)

DEFAULT_TRANSLATE_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

def create_mock_upstream_tables(db_path: pathlib.Path) -> None:
    """Helper to seed the minimal schema required for upstream curate/classify tables."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                canonical_url TEXT,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested', 'draft'))
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS classification_result (
                classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                topic_class TEXT NOT NULL,
                classification_reason TEXT,
                primary_language_code TEXT,
                governmental_involvement INTEGER,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                classified_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_decision (
                curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed')),
                downstream_action TEXT CHECK (downstream_action IS NULL OR downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite', 'reject_discard')),
                decision_reason TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                curated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_output (
                curation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                display_title TEXT NOT NULL,
                summary_short TEXT NOT NULL,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


def five_field_response(title: str, summary: str, b1=None, b2=None, b3=None) -> dict:
    """Builds a translator_v2 five-key mock LLM response payload."""
    return {
        "translated_title": title,
        "translated_summary": summary,
        "translated_bullet_1": b1,
        "translated_bullet_2": b2,
        "translated_bullet_3": b3,
    }


class TestTranslateModule(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        
        # Setup tables
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, DEFAULT_TRANSLATE_MIGRATIONS)

        # Mock translate config setup
        self.config = MagicMock(spec=TranslateConfig)
        self.config.active_provider_name = "test-provider"
        self.config.active_provider = MagicMock()
        self.config.active_provider.model_name = "gpt-5.4-mini"
        self.config.active_provider.api_base = "https://api.test.com"
        self.config.active_provider.api_key_env = "TEST_API_KEY"
        self.config.active_provider.supports_structured_output = False

        self.config.active_template = MagicMock()
        self.config.active_template.version = "translator_v2"
        self.config.active_template.system_instruction = "System Instruction"
        self.config.active_template.user_prompt_template = (
            "Target Lang: {target_language}\n"
            "Title: {display_title}\n"
            "Summary: {summary_short}\n"
            "B1: {bullet_1}\nB2: {bullet_2}\nB3: {bullet_3}"
        )

        self.config.execution_policy = MagicMock()
        self.config.execution_policy.batch_size = 20
        self.config.execution_policy.max_concurrent_requests = 3
        self.config.execution_policy.rate_limit_per_minute = 60
        self.config.execution_policy.request_timeout_seconds = 10.0
        self.config.execution_policy.retry_attempts = 3
        self.config.execution_policy.backoff_factor = 0.1

        self.config.request_defaults = MagicMock()
        self.config.request_defaults.temperature = 0.3
        self.config.request_defaults.top_p = 0.95
        self.config.request_defaults.max_output_tokens = 4096

        self.config.target_languages = {
            "en": MagicMock(label="English", max_title_length=500),
            "ja": MagicMock(label="Japanese", max_title_length=120)
        }

        self.config.validation = MagicMock()
        self.config.validation.default_max_title_length = 500
        self.config.validation.content_ratio_limit = 1.2

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_curation_approval(self, item_id: int, title: str, summary: str, b1: str = None, b2: str = None, b3: str = None, primary_lang: str = None, updated_at: str = "2026-06-20T12:00:00Z") -> None:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO source_item (source_item_id, source_id, title, ingest_status)
                VALUES (?, 1, ?, 'ingested')
            """, (item_id, title))
            
            cursor.execute("""
                INSERT INTO classification_result (source_item_id, topic_class, primary_language_code, model_name, prompt_version, classified_at, created_at)
                VALUES (?, 'core', ?, 'classifier', 'v1', '2026-06-20T11:00:00Z', '2026-06-20T11:00:00Z')
            """, (item_id, primary_lang))
            
            action = "publish_summary" if (b1 and b2 and b3) else "publish_link"
            cursor.execute("""
                INSERT INTO curation_decision (source_item_id, curate_status, downstream_action, model_name, prompt_version, curated_at, created_at)
                VALUES (?, 'approved', ?, 'curator', 'v1', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')
            """, (item_id, action))
            
            cursor.execute("""
                INSERT INTO curation_output (source_item_id, display_title, summary_short, bullet_1, bullet_2, bullet_3, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, '2026-06-20T12:00:00Z', ?)
            """, (item_id, title, summary, b1, b2, b3, updated_at))
            conn.commit()
        finally:
            conn.close()

    def seed_approved_record(self, parent_content_id: int, source_item_id: int, title: str, summary: str,
                             b1=None, b2=None, b3=None, fingerprint: str = "fp_test", language: str = "en") -> None:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO source_item (source_item_id, source_id, title, ingest_status)
                VALUES (?, 1, ?, 'ingested')
            """, (source_item_id, title))
            cursor.execute("""
                INSERT INTO approved_content_record (
                    parent_content_id, source_item_id, display_title, summary_short,
                    bullet_1, bullet_2, bullet_3, content_fingerprint,
                    content_language_code, approved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')
            """, (parent_content_id, source_item_id, title, summary, b1, b2, b3, fingerprint, language))
            conn.commit()
        finally:
            conn.close()

    def make_task(self, parent_content_id: int, source_item_id: int, title: str, summary: str,
                  b1=None, b2=None, b3=None, fingerprint: str = "fp_test",
                  source_language: str = "en", target_language: str = "en", status: str = "new") -> dict:
        """Five-field translation task payload."""
        task = {
            "parent_content_id": parent_content_id,
            "source_item_id": source_item_id,
            "display_title": title,
            "summary_short": summary,
            "bullet_1": b1,
            "bullet_2": b2,
            "bullet_3": b3,
            "content_fingerprint": fingerprint,
            "content_language_code": source_language,
            "language_code": target_language,
        }
        if status != "new":
            task["status"] = status
        return task

    def seed_translation_output(self, parent_content_id: int, source_item_id: int, language_code: str,
                                title=None, summary=None, b1=None, b2=None, b3=None,
                                fingerprint: str = "fp_test", status: str = "completed",
                                model_name: str = "gpt-5.4-mini", prompt_version: str = "translator_v2",
                                translated_at: str = "2026-06-20T12:00:00Z") -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            repo.upsert_translation_output({
                "parent_content_id": parent_content_id,
                "source_item_id": source_item_id,
                "language_code": language_code,
                "display_title": title,
                "summary_short": summary,
                "bullet_1": b1,
                "bullet_2": b2,
                "bullet_3": b3,
                "source_fingerprint": fingerprint,
                "translation_status": status,
                "model_name": model_name,
                "prompt_version": prompt_version,
                "translated_at": translated_at,
            })
            conn.commit()
        finally:
            conn.close()

    def test_handoff_assembler_passthrough_and_fingerprint(self) -> None:
        conn = get_connection(self.db_path)
        try:
            # Seed standard curation approval (with bullets)
            self.seed_curation_approval(
                item_id=10,
                title="Mother-draft Title One",
                summary="This is a brief summary content.",
                b1="Claim content.",
                b2="Evidence content.",
                b3="Impact content.",
                primary_lang="zh",
                updated_at="2026-06-20T12:00:00Z"
            )

            # Seed link curation approval (no bullets)
            self.seed_curation_approval(
                item_id=20,
                title="Mother-draft Title Two",
                summary="This is a link sharing article.",
                b1=None, b2=None, b3=None,
                primary_lang=None,
                updated_at="2026-06-20T12:00:00Z"
            )

            # Run Handoff Assembler
            stats = assemble_approved_content_records(conn)
            self.assertEqual(stats["scanned"], 2)
            self.assertEqual(stats["inserted"], 2)

            # Query approved records
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approved_content_record WHERE source_item_id = 10")
            r1 = cursor.fetchone()
            self.assertIsNotNone(r1)
            self.assertEqual(r1["display_title"], "Mother-draft Title One")
            self.assertEqual(r1["content_language_code"], "en")
            # Straight-through copy: each stored field equals curation_output exactly,
            # with no UI presentation labels injected anywhere.
            self.assertEqual(r1["summary_short"], "This is a brief summary content.")
            self.assertEqual(r1["bullet_1"], "Claim content.")
            self.assertEqual(r1["bullet_2"], "Evidence content.")
            self.assertEqual(r1["bullet_3"], "Impact content.")
            self.assertEqual(
                r1["content_fingerprint"],
                compute_content_fingerprint(
                    "Mother-draft Title One",
                    "This is a brief summary content.",
                    "Claim content.",
                    "Evidence content.",
                    "Impact content."
                )
            )

            cursor.execute("SELECT * FROM approved_content_record WHERE source_item_id = 20")
            r2 = cursor.fetchone()
            self.assertIsNotNone(r2)
            self.assertEqual(r2["display_title"], "Mother-draft Title Two")
            # All curate-originated mother-drafts materialize with content_language_code = 'en'
            self.assertEqual(r2["content_language_code"], "en")
            self.assertEqual(r2["summary_short"], "This is a link sharing article.")
            self.assertIsNone(r2["bullet_1"])
            self.assertIsNone(r2["bullet_2"])
            self.assertIsNone(r2["bullet_3"])

            # Test delta check (no changes, so skipped)
            stats2 = assemble_approved_content_records(conn)
            self.assertEqual(stats2["scanned"], 2)
            self.assertEqual(stats2["skipped"], 2)
            self.assertEqual(stats2["inserted"], 0)
            self.assertEqual(stats2["updated"], 0)

            # Change display_title upstream and update updated_at
            cursor.execute("""
                UPDATE curation_output
                SET display_title = 'New Title', updated_at = '2026-06-21T00:00:00Z'
                WHERE source_item_id = 10
            """)
            conn.commit()

            # Run again (one updated)
            stats3 = assemble_approved_content_records(conn)
            self.assertEqual(stats3["scanned"], 2)
            self.assertEqual(stats3["updated"], 1)
            self.assertEqual(stats3["skipped"], 1)

            cursor.execute("SELECT * FROM approved_content_record WHERE source_item_id = 10")
            r1_new = cursor.fetchone()
            self.assertEqual(r1_new["display_title"], "New Title")
            self.assertNotEqual(r1_new["content_fingerprint"], r1["content_fingerprint"])

        finally:
            conn.close()

    def test_validation_rules(self) -> None:
        source_summary = "This is a body that is long enough to satisfy constraints."

        # Valid response
        valid_data = five_field_response(
            "Translated Title",
            "This is a body that is long enough to satisfy constraints."
        )
        validate_translation_response(
            valid_data, target_language_code="en", source_summary=source_summary,
            source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
            max_title_len=500, content_ratio_limit=1.2
        ) # Should not raise

        # Invalid: Title exceeds limit
        with self.assertRaises(ValueError):
            validate_translation_response(
                valid_data, target_language_code="ja", source_summary=source_summary,
                source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
                max_title_len=5, content_ratio_limit=1.2 # title "Translated Title" length 16 > 5
            )

        # Invalid: Aggregate content ratio exceeds limit
        with self.assertRaises(ValueError):
            validate_translation_response(
                valid_data, target_language_code="en", source_summary=source_summary,
                source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
                max_title_len=500, content_ratio_limit=0.9
            )

    def test_cache_staleness_and_invalidation(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)

            # Insert approved content
            self.seed_approved_record(
                parent_content_id=1, source_item_id=100,
                title="Original Title", summary="Original Summary",
                fingerprint="fp_123", language="zh"
            )

            # Insert matching translation_output
            repo.upsert_translation_output({
                "parent_content_id": 1,
                "source_item_id": 100,
                "language_code": "en",
                "display_title": "Translated English",
                "summary_short": "Translated English Summary",
                "source_fingerprint": "fp_123",
                "translation_status": "completed",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "translator_v2"
            })
            
            # Check stale: no change running model and same fingerprint -> should not mark stale
            staled = repo.detect_and_mark_stale("gpt-5.4-mini", "translator_v2")
            self.assertEqual(len(staled), 0)

            # 1. Config change invalidation -> should mark stale
            staled_config = repo.detect_and_mark_stale("gpt-new-model", "translator_v2")
            self.assertEqual(len(staled_config), 1)
            self.assertEqual(staled_config[0], (1, "en", "config_change"))
            
            # Verify status in database
            out = repo.get_translation_output(1, "en")
            self.assertEqual(out["translation_status"], "stale")

            # Reset back to completed
            repo.upsert_translation_output({
                "parent_content_id": 1,
                "source_item_id": 100,
                "language_code": "en",
                "display_title": "Translated English",
                "summary_short": "Translated English Summary",
                "source_fingerprint": "fp_123",
                "translation_status": "completed",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "translator_v2"
            })

            # 2. Fingerprint change invalidation (upstream edited)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE approved_content_record
                SET content_fingerprint = 'fp_changed'
                WHERE parent_content_id = 1
            """)
            conn.commit()

            staled_fp = repo.detect_and_mark_stale("gpt-5.4-mini", "translator_v2")
            self.assertEqual(len(staled_fp), 1)
            self.assertEqual(staled_fp[0], (1, "en", "fingerprint_mismatch"))
            
            out2 = repo.get_translation_output(1, "en")
            self.assertEqual(out2["translation_status"], "stale")

            # 3. Bypass records config shift exemption
            repo.upsert_translation_output({
                "parent_content_id": 1,
                "source_item_id": 100,
                "language_code": "zh",
                "display_title": "Original Title",
                "summary_short": "Original Summary",
                "source_fingerprint": "fp_changed", # matches record
                "translation_status": "completed",
                "model_name": "bypass",
                "prompt_version": "bypass"
            })
            
            cursor.execute("""
                UPDATE approved_content_record
                SET content_fingerprint = 'fp_changed'
                WHERE parent_content_id = 1
            """)
            conn.commit()

            # Config shift to another model should NOT mark bypass stale
            staled_bypass = repo.detect_and_mark_stale("new-model", "v2")
            # Only 'en' might be stale, 'zh' (bypass) must remain completed
            staled_langs = [x[1] for x in staled_bypass]
            self.assertNotIn("zh", staled_langs)

        finally:
            conn.close()

    @patch("httpx.AsyncClient.post")
    def test_translation_success_and_validation_errors(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            
            self.seed_approved_record(
                parent_content_id=1, source_item_id=100,
                title="中文標題",
                summary="中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。",
                fingerprint="fp_zh", language="zh"
            )

            # 1. Self-translation bypass (target language 'zh' equals mother-draft language 'zh')
            task_bypass = self.make_task(
                parent_content_id=1, source_item_id=100,
                title="中文標題",
                summary="中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。",
                fingerprint="fp_zh", source_language="zh", target_language="zh"
            )

            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            # Execute task_bypass
            success_bypass = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_bypass,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertTrue(success_bypass)
            
            bypass_out = repo.get_translation_output(1, "zh")
            self.assertIsNotNone(bypass_out)
            self.assertEqual(bypass_out["translation_status"], "completed")
            self.assertEqual(bypass_out["model_name"], "bypass")
            self.assertEqual(bypass_out["prompt_version"], "bypass")
            self.assertEqual(bypass_out["display_title"], "中文標題")
            self.assertEqual(bypass_out["summary_short"], "中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。")
            self.assertIsNone(bypass_out["bullet_1"])
            self.assertIsNone(bypass_out["bullet_2"])
            self.assertIsNone(bypass_out["bullet_3"])

            # 2. Mock LLM translation success for 'en'
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps(five_field_response(
                            "Translated Title",
                            "Translated summary content."
                        ))
                    }
                }]
            }
            mock_post.return_value = mock_response

            task_en = self.make_task(
                parent_content_id=1, source_item_id=100,
                title="中文標題",
                summary="中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。",
                fingerprint="fp_zh", source_language="zh", target_language="en"
            )

            success_en = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_en,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertTrue(success_en)
            
            en_out = repo.get_translation_output(1, "en")
            self.assertIsNotNone(en_out)
            self.assertEqual(en_out["translation_status"], "completed")
            self.assertEqual(en_out["model_name"], "gpt-5.4-mini")
            self.assertEqual(en_out["display_title"], "Translated Title")
            self.assertEqual(en_out["summary_short"], "Translated summary content.")
            self.assertIsNone(en_out["bullet_1"])
            self.assertIsNone(en_out["bullet_2"])
            self.assertIsNone(en_out["bullet_3"])

            # 3. Mock LLM translation validation failure (aggregate ratio exceeds)
            mock_ratio_error_response = MagicMock(spec=httpx.Response)
            mock_ratio_error_response.status_code = 200
            mock_ratio_error_response.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps(five_field_response(
                            "長い翻訳タイトル",
                            "Extremely long content rambling content " * 10 # fails aggregate ratio check
                        ))
                    }
                }]
            }
            mock_post.return_value = mock_ratio_error_response

            task_ja = self.make_task(
                parent_content_id=1, source_item_id=100,
                title="中文標題",
                summary="中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。",
                fingerprint="fp_zh", source_language="zh", target_language="ja"
            )

            # First run fails validation -> status='failed', retry_count=1, five fields remain NULL
            success_ja = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_ja,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertFalse(success_ja)
            
            ja_out = repo.get_translation_output(1, "ja")
            self.assertIsNotNone(ja_out)
            self.assertEqual(ja_out["translation_status"], "failed")
            self.assertEqual(ja_out["retry_count"], 1)
            self.assertIsNone(ja_out["display_title"])
            self.assertIsNone(ja_out["summary_short"])
            self.assertIsNone(ja_out["bullet_1"])
            self.assertIsNone(ja_out["bullet_2"])
            self.assertIsNone(ja_out["bullet_3"])

            # 4. Operator Forced Rerun Failure rollback check
            # Seed a completed translation first
            repo.upsert_translation_output({
                "parent_content_id": 1,
                "source_item_id": 100,
                "language_code": "ja",
                "display_title": "Old Valid Title",
                "summary_short": "Old Valid Summary",
                "bullet_1": "Old Valid Bullet 1",
                "bullet_2": "Old Valid Bullet 2",
                "bullet_3": "Old Valid Bullet 3",
                "source_fingerprint": "fp_zh",
                "translation_status": "completed",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "translator_v2",
                "translated_at": "2026-06-20T12:00:00Z"
            })
            conn.commit()

            # Run again with same bad validation API response (should fail rerun)
            task_ja_forced = self.make_task(
                parent_content_id=1, source_item_id=100,
                title="中文標題",
                summary="中文內容是一篇非常重要的未確認異常現象報告，其中包含了許多關鍵細節。",
                fingerprint="fp_zh", source_language="zh", target_language="ja",
                status="completed"
            )
            success_ja_rerun = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_ja_forced,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertFalse(success_ja_rerun)

            # Rerun failure must NOT overwrite completion or increment retry count
            ja_out_after_fail = repo.get_translation_output(1, "ja")
            self.assertEqual(ja_out_after_fail["translation_status"], "completed")
            self.assertEqual(ja_out_after_fail["display_title"], "Old Valid Title")
            self.assertEqual(ja_out_after_fail["summary_short"], "Old Valid Summary")
            self.assertEqual(ja_out_after_fail["bullet_1"], "Old Valid Bullet 1")
            self.assertEqual(ja_out_after_fail["bullet_2"], "Old Valid Bullet 2")
            self.assertEqual(ja_out_after_fail["bullet_3"], "Old Valid Bullet 3")
            self.assertEqual(ja_out_after_fail["retry_count"], 0)

        finally:
            conn.close()

    def test_delta_prescreen_with_upstream_timestamps(self) -> None:
        conn = get_connection(self.db_path)
        try:
            # Time 1: Initial curation output updated at Time 1
            self.seed_curation_approval(
                item_id=200,
                title="Title A",
                summary="Summary text A",
                updated_at="2026-06-21T00:00:00Z"
            )

            # First run: inserts record and stores updated_at as 2026-06-21T00:00:00Z
            stats1 = assemble_approved_content_records(conn)
            self.assertEqual(stats1["inserted"], 1)

            # Check database value: approved_content_record.author_metadata should contain upstream_updated_at
            cursor = conn.cursor()
            cursor.execute("SELECT author_metadata, updated_at FROM approved_content_record WHERE source_item_id = 200")
            r1 = cursor.fetchone()
            self.assertIsNotNone(r1["updated_at"])
            meta1 = json.loads(r1["author_metadata"])
            self.assertEqual(meta1["upstream_updated_at"], "2026-06-21T00:00:00Z")

            # Second run immediately: should be skipped
            stats2 = assemble_approved_content_records(conn)
            self.assertEqual(stats2["skipped"], 1)

            # Time 2: Curation output updated at Time 2 (which is 2026-06-21T01:00:00Z)
            # This is later than the stored 00:00:00Z, so it must be processed.
            cursor.execute("""
                UPDATE curation_output
                SET display_title = 'Updated Title A', updated_at = '2026-06-21T01:00:00Z'
                WHERE source_item_id = 200
            """)
            conn.commit()

            stats3 = assemble_approved_content_records(conn)
            self.assertEqual(stats3["updated"], 1)
            
            cursor.execute("SELECT display_title, author_metadata, updated_at FROM approved_content_record WHERE source_item_id = 200")
            r2 = cursor.fetchone()
            self.assertEqual(r2["display_title"], "Updated Title A")
            self.assertIsNotNone(r2["updated_at"])
            meta2 = json.loads(r2["author_metadata"])
            self.assertEqual(meta2["upstream_updated_at"], "2026-06-21T01:00:00Z")
        finally:
            conn.close()

    @patch("httpx.AsyncClient.post")
    def test_distinguish_stale_failure_vs_forced_failure(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(
                parent_content_id=3, source_item_id=300,
                title="Source Title", summary="Source Summary",
                fingerprint="fp_300", language="zh"
            )

            # Seed a completed translation first
            repo.upsert_translation_output({
                "parent_content_id": 3,
                "source_item_id": 300,
                "language_code": "en",
                "display_title": "Old English Title",
                "summary_short": "Old English Summary",
                "source_fingerprint": "fp_300",
                "translation_status": "completed",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "translator_v2"
            })
            conn.commit()

            # Mock LLM API failure
            mock_post.side_effect = httpx.ConnectError("API unavailable")
            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            # Case A: Normal stale retranslation failure. 
            # First, we mark it as stale in the database:
            repo.update_translation_status(parent_content_id=3, language_code="en", status="stale")
            conn.commit()
            task_stale = self.make_task(
                parent_content_id=3, source_item_id=300,
                title="Source Title", summary="Source Summary",
                fingerprint="fp_300", source_language="zh", target_language="en",
                status="stale"  # Task status is stale, NOT completed
            )
            success_stale = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_stale,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertFalse(success_stale)
            
            # Stale translation failure MUST write failed and increment retry_count
            out_stale = repo.get_translation_output(3, "en")
            self.assertEqual(out_stale["translation_status"], "failed")
            self.assertEqual(out_stale["retry_count"], 1)

            # Reset back to completed
            repo.upsert_translation_output({
                "parent_content_id": 3,
                "source_item_id": 300,
                "language_code": "en",
                "display_title": "Old English Title",
                "summary_short": "Old English Summary",
                "source_fingerprint": "fp_300",
                "translation_status": "completed",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "translator_v2"
            })
            conn.commit()

            # Case B: Explicit operator forced rerun failure (--force)
            task_forced = self.make_task(
                parent_content_id=3, source_item_id=300,
                title="Source Title", summary="Source Summary",
                fingerprint="fp_300", source_language="zh", target_language="en",
                status="completed"  # Task status is completed
            )
            success_forced = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_forced,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertFalse(success_forced)

            # Forced rerun failure MUST NOT write failed, MUST preserve the completed state, and retry_count must remain 0
            out_forced = repo.get_translation_output(3, "en")
            self.assertEqual(out_forced["translation_status"], "completed")
            self.assertEqual(out_forced["display_title"], "Old English Title")
            self.assertEqual(out_forced["summary_short"], "Old English Summary")
            self.assertEqual(out_forced["retry_count"], 0)

        finally:
            conn.close()

    def test_cli_commands_verification(self) -> None:
        from click.testing import CliRunner
        from modules.translate.src.cli import cli

        runner = CliRunner()
        # Verify CLI validate
        res_val = runner.invoke(cli, ["validate"])
        self.assertEqual(res_val.exit_code, 0)
        self.assertIn("Configuration validated successfully", res_val.output)

        # Seed an item and run assemble via CLI first to exercise non-empty queue paths
        self.seed_curation_approval(
            item_id=500,
            title="UAP Sighting Over Base",
            summary="A structured report describing military sightings.",
            b1="Military personnel observed unidentified objects.",
            b2="Multiple visual and radar witnesses.",
            b3="No threat was detected.",
            primary_lang="zh"
        )
        res_assemble = runner.invoke(cli, ["assemble", "--db-path", str(self.db_path)])
        self.assertEqual(res_assemble.exit_code, 0)
        self.assertIn("HANDOFF ASSEMBLY COMPLETED", res_assemble.output)

        # Verify CLI status now displays the seeded pending items
        res_status = runner.invoke(cli, ["status", "--db-path", str(self.db_path)])
        self.assertEqual(res_status.exit_code, 0)
        self.assertIn("TRANSLATE QUEUE STATUS SUMMARY", res_status.output)
        self.assertIn("pending (eligible total):  1", res_status.output)

        # Verify CLI run --preview-prompts exercises non-empty prompt preview paths
        res_run_prev = runner.invoke(cli, ["run", "--db-path", str(self.db_path), "--preview-prompts", "--batch-size", "1"])
        self.assertEqual(res_run_prev.exit_code, 0)
        self.assertIn("PREVIEW TRANSLATION PROMPT:", res_run_prev.output)
        self.assertIn("UAP Sighting Over Base", res_run_prev.output)
        self.assertIn("Military personnel observed unidentified objects.", res_run_prev.output)

    def test_cjk_script_validation(self) -> None:
        source_summary = "This is a body of text."

        # 1. Chinese (zh) script validation
        valid_zh = five_field_response("中文標題", "這是一段中文翻譯內容。")
        # CJK characters present in aggregated content -> should pass
        validate_translation_response(
            valid_zh, target_language_code="zh", source_summary=source_summary,
            source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
            max_title_len=120, content_ratio_limit=1.2
        )

        invalid_zh = five_field_response(
            "Translated Title",
            "This is a body of text in English which copied the source content."
        )
        # No CJK characters -> should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            validate_translation_response(
                invalid_zh, target_language_code="zh", source_summary=source_summary,
                source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
                max_title_len=120, content_ratio_limit=5.0
            )
        self.assertIn("lacks CJK Unified Ideographs", str(ctx.exception))

        # 2. Japanese (ja) script validation
        valid_ja = five_field_response("日本語タイトル", "これは日本語の翻訳コンテンツです。")
        # Hiragana/Katakana present -> should pass
        validate_translation_response(
            valid_ja, target_language_code="ja", source_summary=source_summary,
            source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
            max_title_len=120, content_ratio_limit=2.0
        )

        # Mixed script proper noun tolerance: Japanese containing "AARO" and "UAP"
        valid_ja_mixed = five_field_response("日本語タイトル", "AAROによるUAPに関する報告書。")
        validate_translation_response(
            valid_ja_mixed, target_language_code="ja", source_summary=source_summary,
            source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
            max_title_len=120, content_ratio_limit=2.0
        )

        invalid_ja = five_field_response(
            "Translated Title",
            "This is a body of text in English which copied the source content."
        )
        # No Hiragana/Katakana -> should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            validate_translation_response(
                invalid_ja, target_language_code="ja", source_summary=source_summary,
                source_bullet_1=None, source_bullet_2=None, source_bullet_3=None,
                max_title_len=120, content_ratio_limit=5.0
            )
        self.assertIn("lacks Hiragana/Katakana characters", str(ctx.exception))

    @patch("httpx.AsyncClient.post")
    def test_bypass_policy_under_new_mother_draft_language(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(
                parent_content_id=6, source_item_id=600,
                title="English Title", summary="English summary content.",
                fingerprint="fp_en", language="en"
            )

            # Mock LLM API response for translations that are not bypassed
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps(five_field_response(
                            "中文標題",
                            "這是一段中文翻譯內容。"
                        ))
                    }
                }]
            }
            mock_post.return_value = mock_response

            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            # 1. Target language 'zh' should NOT bypass since target 'zh' != source 'en'
            task_zh = self.make_task(
                parent_content_id=6, source_item_id=600,
                title="English Title", summary="English summary content.",
                fingerprint="fp_en", source_language="en", target_language="zh"
            )
            
            success_zh = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_zh,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertTrue(success_zh)
            # Verify it actually hit the LLM (model name is gpt-5.4-mini, not bypass)
            zh_out = repo.get_translation_output(6, "zh")
            self.assertEqual(zh_out["model_name"], "gpt-5.4-mini")
            self.assertEqual(zh_out["display_title"], "中文標題")
            self.assertEqual(zh_out["summary_short"], "這是一段中文翻譯內容。")

            # 2. Target language 'en' SHOULD bypass since target 'en' == source 'en'
            task_en = self.make_task(
                parent_content_id=6, source_item_id=600,
                title="English Title", summary="English summary content.",
                fingerprint="fp_en", source_language="en", target_language="en"
            )
            success_en = asyncio.run(translate_task(
                repo=repo, client=client, config=self.config, task=task_en,
                api_key="mock", db_lock=db_lock, commit=True
            ))
            self.assertTrue(success_en)
            # Verify it bypassed LLM (model name is bypass)
            en_out = repo.get_translation_output(6, "en")
            self.assertEqual(en_out["model_name"], "bypass")
            self.assertEqual(en_out["display_title"], "English Title")
            self.assertEqual(en_out["summary_short"], "English summary content.")
            self.assertIsNone(en_out["bullet_1"])
            self.assertIsNone(en_out["bullet_2"])
            self.assertIsNone(en_out["bullet_3"])

        finally:
            conn.close()
