"""Direct regression tests for validate_classification_response().

Under the mini-proxy `json_object` fallback this validator is the only
schema gate between raw model output and the canonical DB.
"""

import math
import unittest

from modules.classify.src.orchestrator import validate_classification_response
from modules.classify.tests.helpers import valid_llm_response


class TestStableFieldValidation(unittest.TestCase):
    def test_accepts_canonical_response_and_coerces_types(self) -> None:
        stable, _ = validate_classification_response(
            valid_llm_response(classification_confidence=1, governmental_involvement=0)
        )
        self.assertEqual(stable["classification_confidence"], 1.0)
        self.assertIsInstance(stable["classification_confidence"], float)
        self.assertEqual(stable["governmental_involvement"], 0)
        self.assertIsInstance(stable["governmental_involvement"], int)

    def test_rejects_boolean_confidence(self) -> None:
        # Python bool is a subclass of int; JSON true/false must not pass as numbers.
        for bad in (True, False):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_classification_response(valid_llm_response(classification_confidence=bad))

    def test_rejects_boolean_governmental_involvement(self) -> None:
        for bad in (True, False):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_classification_response(valid_llm_response(governmental_involvement=bad))

    def test_rejects_string_numeric_confidence(self) -> None:
        with self.assertRaises(ValueError):
            validate_classification_response(valid_llm_response(classification_confidence="0.9"))

    def test_rejects_nan_and_infinite_confidence(self) -> None:
        # Python's json module parses NaN/Infinity literals, so the validator
        # is the only line of defense against non-finite floats.
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_classification_response(valid_llm_response(classification_confidence=bad))

    def test_rejects_out_of_range_confidence(self) -> None:
        for bad in (-0.1, 1.1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_classification_response(valid_llm_response(classification_confidence=bad))

    def test_rejects_blank_language_code(self) -> None:
        for bad in ("", "   ", None, 7):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_classification_response(valid_llm_response(primary_language_code=bad))

    def test_reason_max_length_contract(self) -> None:
        stable, _ = validate_classification_response(
            valid_llm_response(classification_reason="x" * 300)
        )
        self.assertEqual(len(stable["classification_reason"]), 300)
        with self.assertRaises(ValueError):
            validate_classification_response(valid_llm_response(classification_reason="x" * 301))


class TestExperimentalSignalExtraction(unittest.TestCase):
    def test_absent_experimental_signals_write_nothing(self) -> None:
        response = valid_llm_response()
        del response["content_timeliness"]
        del response["primary_evidence_type"]
        _, extra = validate_classification_response(response)
        self.assertEqual(extra, {})

    def test_null_experimental_signals_write_nothing(self) -> None:
        _, extra = validate_classification_response(
            valid_llm_response(content_timeliness=None, primary_evidence_type=None)
        )
        self.assertEqual(extra, {})

    def test_valid_experimental_signals_captured(self) -> None:
        _, extra = validate_classification_response(valid_llm_response())
        self.assertEqual(extra["content_timeliness"], "current")
        self.assertEqual(extra["primary_evidence_type"], "radar_sensor")

    def test_unknown_keys_discarded(self) -> None:
        stable, extra = validate_classification_response(
            valid_llm_response(unauthorized_key="some_value", another_unknown=123)
        )
        self.assertNotIn("unauthorized_key", extra)
        self.assertNotIn("another_unknown", extra)
        self.assertNotIn("unauthorized_key", stable)
        self.assertNotIn("another_unknown", stable)


if __name__ == "__main__":
    unittest.main()
