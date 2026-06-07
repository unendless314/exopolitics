import unittest
import tempfile
import pathlib
from modules.classify.src.config import load_classify_config, ClassifyConfig

class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = pathlib.Path(self.temp_dir.name)
        
        self.settings_yaml = """
active_provider: openai
active_prompt_template: single_item_v2

request_defaults:
  temperature: 0.15
  top_p: 0.9
  max_output_tokens: 512

execution_policy:
  batch_size: 15
  max_concurrent_requests: 4
  rate_limit_per_minute: 40
  request_timeout_seconds: 8.5
  min_context_characters: 120
  retry_attempts: 2
  backoff_factor: 1.5

providers:
  openai:
    api_type: openai
    api_key_env: TEST_OPENAI_API_KEY
    model_name: test-gpt-mini
    supports_structured_output: true
    api_base: https://api.openai.com/v1

deterministic_classification:
  model_name: test-deterministic-low-context
  prompt_version: test_rule_v1
"""
        self.settings_file = self.temp_path / "model_settings.yaml"
        self.settings_file.write_text(self.settings_yaml, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_classify_config(self) -> None:
        config = load_classify_config(self.settings_file)
        
        self.assertIsInstance(config, ClassifyConfig)
        self.assertEqual(config.active_provider, "openai")
        self.assertEqual(config.active_prompt_template, "single_item_v2")
        
        # Request Defaults
        self.assertEqual(config.request_defaults.temperature, 0.15)
        self.assertEqual(config.request_defaults.top_p, 0.9)
        self.assertEqual(config.request_defaults.max_output_tokens, 512)
        
        # Execution Policy
        self.assertEqual(config.execution_policy.batch_size, 15)
        self.assertEqual(config.execution_policy.max_concurrent_requests, 4)
        self.assertEqual(config.execution_policy.rate_limit_per_minute, 40)
        self.assertEqual(config.execution_policy.request_timeout_seconds, 8.5)
        self.assertEqual(config.execution_policy.min_context_characters, 120)
        self.assertEqual(config.execution_policy.retry_attempts, 2)
        self.assertEqual(config.execution_policy.backoff_factor, 1.5)
        
        # Providers
        self.assertIn("openai", config.providers)
        openai_prov = config.providers["openai"]
        self.assertEqual(openai_prov.api_type, "openai")
        self.assertEqual(openai_prov.api_key_env, "TEST_OPENAI_API_KEY")
        self.assertEqual(openai_prov.model_name, "test-gpt-mini")
        self.assertTrue(openai_prov.supports_structured_output)
        self.assertEqual(openai_prov.api_base, "https://api.openai.com/v1")
        
        # Deterministic
        self.assertEqual(config.deterministic_classification.model_name, "test-deterministic-low-context")
        self.assertEqual(config.deterministic_classification.prompt_version, "test_rule_v1")

if __name__ == "__main__":
    unittest.main()
