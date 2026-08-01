"""
Config loader and schema validation tests (plan section 3.7,
DATA_CONTRACT.md section 9.2).

Structural or schema configuration errors must abort immediately with a
clear error, before any database or export directory is touched.
"""

import pathlib
import tempfile
import unittest
from typing import Any, Dict

import yaml

from modules.publish.src.config import (
    PublishSettingsYaml,
    ExecutionPolicy,
    IndexPolicy,
    validate_and_load_config,
)


def valid_settings_dict() -> Dict[str, Any]:
    return {
        "target_languages": {"zh": "Traditional Chinese", "en": "English"},
        "coverage_policy": "strict_match",
        "execution_policy": {"default_export_dir": "data/publish_export", "batch_size": 10},
        "index_policy": {"latest_limit": 5, "archive_granularity": "month"},
    }


class TestPydanticValidation(unittest.TestCase):
    """Direct model-level validation rules."""

    def assert_settings_rejected(self, settings: Dict[str, Any], error_token: str) -> None:
        with self.assertRaises(Exception) as ctx:
            PublishSettingsYaml(**settings)
        self.assertIn(error_token, str(ctx.exception))

    def test_empty_target_languages_rejected(self) -> None:
        settings = valid_settings_dict()
        settings["target_languages"] = {}
        self.assert_settings_rejected(settings, "target_languages must contain a non-empty dictionary")

    def test_unknown_coverage_policy_rejected(self) -> None:
        settings = valid_settings_dict()
        settings["coverage_policy"] = "best_effort"
        self.assert_settings_rejected(settings, "coverage_policy must be 'strict_match'")

    def test_non_positive_batch_size_rejected(self) -> None:
        for bad in (0, -3):
            with self.subTest(batch_size=bad):
                with self.assertRaises(Exception) as ctx:
                    ExecutionPolicy(batch_size=bad)
                self.assertIn("batch_size must be a positive integer greater than zero", str(ctx.exception))

    def test_non_positive_latest_limit_rejected(self) -> None:
        for bad in (0, -1):
            with self.subTest(latest_limit=bad):
                with self.assertRaises(Exception) as ctx:
                    IndexPolicy(latest_limit=bad)
                self.assertIn("latest_limit must be a positive integer greater than zero", str(ctx.exception))

    def test_non_month_archive_granularity_rejected(self) -> None:
        for bad in ("week", "year"):
            with self.subTest(archive_granularity=bad):
                with self.assertRaises(Exception) as ctx:
                    IndexPolicy(archive_granularity=bad)
                self.assertIn("archive_granularity must equal 'month'", str(ctx.exception))

    def test_missing_required_sections_rejected(self) -> None:
        for missing_key in ("target_languages", "execution_policy", "index_policy"):
            with self.subTest(missing_key=missing_key):
                settings = valid_settings_dict()
                del settings[missing_key]
                with self.assertRaises(Exception) as ctx:
                    PublishSettingsYaml(**settings)
                self.assertIn(missing_key, str(ctx.exception))


class TestConfigFileLoader(unittest.TestCase):
    """File-level loading rules for validate_and_load_config()."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = pathlib.Path(self.temp_dir.name) / "publish_settings.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_yaml(self, content: Any) -> None:
        if isinstance(content, str):
            self.config_path.write_text(content, encoding="utf-8")
        else:
            self.config_path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")

    def test_missing_config_file_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            validate_and_load_config(self.config_path)

    def test_non_mapping_yaml_rejected(self) -> None:
        self.write_yaml("- just\n- a\n- list\n")
        with self.assertRaises(ValueError) as ctx:
            validate_and_load_config(self.config_path)
        self.assertIn("must be a mapping", str(ctx.exception))

    def test_empty_yaml_rejected(self) -> None:
        self.write_yaml("")
        with self.assertRaises(ValueError) as ctx:
            validate_and_load_config(self.config_path)
        self.assertIn("must be a mapping", str(ctx.exception))

    def test_scalar_yaml_rejected(self) -> None:
        self.write_yaml("42\n")
        with self.assertRaises(ValueError) as ctx:
            validate_and_load_config(self.config_path)
        self.assertIn("must be a mapping", str(ctx.exception))

    def test_loader_propagates_schema_errors(self) -> None:
        settings = valid_settings_dict()
        settings["execution_policy"]["batch_size"] = 0
        self.write_yaml(settings)
        with self.assertRaises(Exception) as ctx:
            validate_and_load_config(self.config_path)
        self.assertIn("batch_size must be a positive integer greater than zero", str(ctx.exception))

    def test_valid_config_loads(self) -> None:
        self.write_yaml(valid_settings_dict())
        config = validate_and_load_config(self.config_path)
        self.assertEqual({"zh": "Traditional Chinese", "en": "English"}, config.target_languages)
        self.assertEqual("strict_match", config.coverage_policy)
        self.assertEqual(10, config.execution_policy.batch_size)
        self.assertEqual(5, config.index_policy.latest_limit)


if __name__ == "__main__":
    unittest.main()
