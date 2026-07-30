import json
import unittest

from modules.curate.src.orchestrator import validate_curation_response
from modules.curate.tests.support import make_valid_response

ALL_ACTIONS = ("publish_link", "publish_summary", "edit_rewrite", "reject_discard")


def _roundtrip(payload):
    """Deep-copies a response payload through JSON, mirroring real parsing."""
    return json.loads(json.dumps(payload))


class TestResponseValidator(unittest.TestCase):
    """Direct contract tests for validate_curation_response().

    The local validator is the last schema gate before model output enters
    the canonical DB on the `json_object` fallback path, so top-level
    presence, per-action nullability, and field types are locked here.
    """

    def test_missing_top_level_fields_rejected(self):
        for missing in ("curation_decision", "editor_brief", "curation_output"):
            with self.subTest(missing=missing):
                payload = make_valid_response("reject_discard")
                del payload[missing]
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_missing_nullable_top_level_fields_rejected_for_all_actions(self):
        # Even when an action allows null brief/output, the keys must exist.
        for action in ALL_ACTIONS:
            for missing in ("editor_brief", "curation_output"):
                with self.subTest(action=action, missing=missing):
                    payload = make_valid_response(action)
                    del payload[missing]
                    with self.assertRaises(ValueError):
                        validate_curation_response(payload)

    def test_top_level_type_errors_rejected(self):
        bad_cases = [
            ("curation_decision", "not-a-dict"),
            ("curation_decision", None),
            ("curation_decision", ["approved"]),
            ("editor_brief", "not-a-dict"),
            ("editor_brief", 42),
            ("curation_output", "not-a-dict"),
            ("curation_output", 42),
        ]
        for field, bad_value in bad_cases:
            with self.subTest(field=field, bad_value=bad_value):
                payload = make_valid_response("publish_summary")
                payload[field] = bad_value
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_non_dict_response_rejected(self):
        for bad in (None, [], "string", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_curation_response(bad)

    def test_valid_matrix_all_actions(self):
        for action in ALL_ACTIONS:
            with self.subTest(action=action):
                validate_curation_response(make_valid_response(action))

    def test_publish_link_bullets_must_be_null(self):
        for bullet in ("bullet_1", "bullet_2", "bullet_3"):
            with self.subTest(bullet=bullet):
                payload = make_valid_response("publish_link")
                payload["curation_output"][bullet] = "not null"
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_publish_summary_bullets_must_be_non_empty_strings(self):
        for bullet in ("bullet_1", "bullet_2", "bullet_3"):
            for bad_value in (None, "", "   ", 42):
                with self.subTest(bullet=bullet, bad_value=bad_value):
                    payload = make_valid_response("publish_summary")
                    payload["curation_output"][bullet] = bad_value
                    with self.assertRaises(ValueError):
                        validate_curation_response(payload)

    def test_target_format_must_match_action(self):
        with self.assertRaises(ValueError):
            validate_curation_response(
                make_valid_response("publish_link", brief_overrides={"target_format": "structured_summary"})
            )
        with self.assertRaises(ValueError):
            validate_curation_response(
                make_valid_response("publish_summary", brief_overrides={"target_format": "link_card"})
            )

    def test_edit_rewrite_must_not_have_output(self):
        payload = make_valid_response("edit_rewrite")
        payload["curation_output"] = {"display_title": "T", "summary_short": "S"}
        with self.assertRaises(ValueError):
            validate_curation_response(payload)

    def test_reject_discard_must_not_have_brief_or_output(self):
        for field in ("editor_brief", "curation_output"):
            with self.subTest(field=field):
                payload = make_valid_response("reject_discard")
                payload[field] = {"display_title": "T"} if field == "curation_output" else {"brief_goal": "G"}
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_status_action_alignment(self):
        for status, action in (
            ("approved", "edit_rewrite"),
            ("approved", "reject_discard"),
            ("rejected", "publish_link"),
            ("rejected", "publish_summary"),
        ):
            with self.subTest(status=status, action=action):
                payload = make_valid_response(action, decision_overrides={"curate_status": status})
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_invalid_enums_rejected(self):
        with self.assertRaises(ValueError):
            validate_curation_response(
                make_valid_response("reject_discard", decision_overrides={"curate_status": "failed"})
            )
        with self.assertRaises(ValueError):
            validate_curation_response(
                make_valid_response("reject_discard", decision_overrides={"downstream_action": "archive"})
            )

    def test_decision_required_keys_and_reason_length(self):
        for missing in ("curate_status", "downstream_action", "decision_reason"):
            with self.subTest(missing=missing):
                payload = make_valid_response("reject_discard")
                del payload["curation_decision"][missing]
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

        for bad_reason in ("x" * 251, 42):
            with self.subTest(bad_reason=bad_reason):
                payload = make_valid_response("reject_discard", decision_overrides={"decision_reason": bad_reason})
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

    def test_brief_required_keys_and_risk_flags_type(self):
        for missing in ("brief_goal", "target_format", "risk_flags", "tone_guidance"):
            with self.subTest(missing=missing):
                payload = make_valid_response("publish_summary")
                del payload["editor_brief"][missing]
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

        payload = make_valid_response("publish_summary", brief_overrides={"risk_flags": "clickbait"})
        with self.assertRaises(ValueError):
            validate_curation_response(payload)

    def test_output_required_keys_and_length_limits(self):
        for missing in ("display_title", "summary_short"):
            with self.subTest(missing=missing):
                payload = make_valid_response("publish_link")
                del payload["curation_output"][missing]
                with self.assertRaises(ValueError):
                    validate_curation_response(payload)

        payload = make_valid_response("publish_link", output_overrides={"display_title": "x" * 251})
        with self.assertRaises(ValueError):
            validate_curation_response(payload)

        payload = make_valid_response("publish_link", output_overrides={"summary_short": "x" * 501})
        with self.assertRaises(ValueError):
            validate_curation_response(payload)

    def test_unknown_keys_accepted_on_fallback_path(self):
        # Decided compatibility behavior: the json_object fallback validator
        # deliberately tolerates unknown top-level and nested keys.
        payload = _roundtrip(make_valid_response("publish_summary"))
        payload["unexpected_top_level"] = {"anything": True}
        payload["curation_decision"]["unexpected_nested"] = "extra"
        payload["editor_brief"]["unexpected_nested"] = 123
        payload["curation_output"]["unexpected_nested"] = ["extra"]
        validate_curation_response(payload)  # must not raise


if __name__ == "__main__":
    unittest.main()
