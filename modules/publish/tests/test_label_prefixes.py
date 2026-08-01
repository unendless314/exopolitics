"""
Parameterized presentation label-prefix validation (plan section 3.11,
DATA_CONTRACT.md section 6.1).

Every documented label variant must be rejected when it prefixes a content
value with an ASCII or fullwidth colon, with optional Markdown emphasis or
leading whitespace/list markers, in both ``summary_short`` and semantic
``bullets`` values. Non-prefix occurrences remain valid content.

The label list is the fixed test-local copy in test_item_payload_contract
(kept independent from the production tuple and the JSON schema on purpose,
so the three lists are checked against each other).
"""

import copy
import unittest
from typing import Any, Dict

from modules.publish.src.orchestrator import (
    validate_item_payload,
    ValidationError,
)
from modules.publish.tests.test_item_payload_contract import UI_LABELS

BASE_PAYLOAD: Dict[str, Any] = {
    "source_item_id": 1,
    "language_code": "en",
    "slug": "label-item",
    "display_title": "Label Item",
    "summary_short": "Summary.",
    "bullets": {"key_claim": "Claim.", "evidence_level": "Evidence.", "objective_impact": "Impact."},
    "canonical_url": "https://example.com/1",
    "source_published_at": "2026-06-15T12:00:00Z",
    "approved_at": "2026-06-20T12:00:00Z",
    "published_at": "2026-07-01T00:00:00Z",
    "downstream_action": "publish_summary",
    "disclosure_note": "This item is AI-assisted and human-curated.",
    "author_metadata": {"source_module": "edit", "writer_type": "human", "editor": "john_doe"},
}

PREFIX_VARIANTS = (
    ("ascii_colon", "{label}: value"),
    ("fullwidth_colon", "{label}：value"),
    ("markdown_emphasis", "**{label}**: value"),
    ("leading_whitespace", "  {label}: value"),
    ("list_marker", "- {label}: value"),
)


def payload_with_summary(summary: str) -> Dict[str, Any]:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["summary_short"] = summary
    return payload


def payload_with_bullet(bullet_value: str) -> Dict[str, Any]:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["bullets"]["key_claim"] = bullet_value
    return payload


class TestLabelPrefixValidation(unittest.TestCase):
    def test_label_prefix_variants_rejected_in_summary_and_bullets(self) -> None:
        for label in UI_LABELS:
            for variant_name, template in PREFIX_VARIANTS:
                value = template.format(label=label)
                with self.subTest(label=label, variant=variant_name, field="summary_short"):
                    with self.assertRaises(ValidationError):
                        validate_item_payload(payload_with_summary(value))
                with self.subTest(label=label, variant=variant_name, field="bullets.key_claim"):
                    with self.assertRaises(ValidationError):
                        validate_item_payload(payload_with_bullet(value))

    def test_non_prefix_label_occurrences_accepted(self) -> None:
        for label in UI_LABELS:
            accepted_values = (
                f"The {label} is discussed here",          # not at string start
                f"value mentions {label}: mid-string",     # label mid-string
                f"{label} without a colon",                # no colon
                f"{label} is not a prefix: later colon",   # colon not adjacent to label
            )
            for value in accepted_values:
                with self.subTest(label=label, value=value, field="summary_short"):
                    validate_item_payload(payload_with_summary(value))  # must not raise
                with self.subTest(label=label, value=value, field="bullets.key_claim"):
                    validate_item_payload(payload_with_bullet(value))   # must not raise


if __name__ == "__main__":
    unittest.main()
