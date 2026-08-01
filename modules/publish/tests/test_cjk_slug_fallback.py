"""
End-to-end CJK slug fallback tests (plan section 3.12, DATA_CONTRACT.md
section 7).

A title that slugifies to the empty string (for example an all-CJK English
title) must fall back to the ``item`` base slug inside ``generate_slug()``,
collisions must suffix deterministically (``item-2``), and the fallback slug
must stay frozen across later republications. Route identity is an
observable contract, so this is pinned end to end rather than only at the
slugify helper level.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.tests import support


class TestCjkSlugFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_cjk_item(self, item_id: int, cjk_title: str) -> None:
        support.seed_item(
            self.db_path, item_id, cjk_title, "2026-06-15T12:00:00Z",
            translations={
                "zh": {"display_title": cjk_title},
                "en": {"display_title": cjk_title},
            },
        )

    def get_slug(self, item_id: int) -> str:
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        slug = repo.get_publish_record_by_source_item_id(item_id)["slug"]
        conn.close()
        return slug

    def test_cjk_titles_fall_back_and_stay_frozen(self) -> None:
        # First all-CJK item: every language artifact shares the "item" slug.
        self.seed_cjk_item(1, "幽浮目擊事件")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)
        self.assertEqual("item", self.get_slug(1))
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertTrue((self.export_dir / lang / "items" / "item.json").exists())

        # Second all-CJK item: deterministic collision suffix "item-2".
        self.seed_cjk_item(2, "秘密檔案公開")
        summary2 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary2["published_count"], 2)
        self.assertEqual("item-2", self.get_slug(2))
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertTrue((self.export_dir / lang / "items" / "item-2.json").exists())

        # Retitle both items to ASCII titles with new fingerprints and
        # republish: both fallback slugs remain frozen.
        conn = get_connection(self.db_path)
        conn.execute("UPDATE approved_content_record SET content_fingerprint = 'fp_v2' WHERE source_item_id IN (1, 2)")
        conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_v2', display_title = 'UFO Sighting Event' WHERE source_item_id = 1")
        conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_v2', display_title = 'Secret Files Disclosure' WHERE source_item_id = 2")
        conn.commit()
        conn.close()

        summary3 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary3["published_count"], 4)
        self.assertEqual("item", self.get_slug(1))
        self.assertEqual("item-2", self.get_slug(2))
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertTrue((self.export_dir / lang / "items" / "item.json").exists())
                self.assertTrue((self.export_dir / lang / "items" / "item-2.json").exists())


if __name__ == "__main__":
    unittest.main()
