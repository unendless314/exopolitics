"""Shared setup helpers for DB-backed integration tests.

Centralizes the temporary config directory layout (categories.yaml,
retention_policy.yaml, sources.yaml) and the migrations directory path so
each integration test file declares only the sources it needs. Feed payload
samples live in feed_samples.py.

Helpers here only assemble files and paths; test-specific preconditions
(custom source sets, seeded state) stay visible in the test files themselves.
"""

import pathlib

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

CATEGORIES_YAML = """schema_version: 1
categories:
  1:
    name: Test Category
    slug: test-cat
    enabled: true
"""

RETENTION_POLICY_YAML = """schema_version: 1
raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
"""


def write_base_config(config_dir: pathlib.Path) -> None:
    """Writes categories.yaml and retention_policy.yaml into config_dir."""
    with open(config_dir / "categories.yaml", "w", encoding="utf-8") as f:
        f.write(CATEGORIES_YAML)
    with open(config_dir / "retention_policy.yaml", "w", encoding="utf-8") as f:
        f.write(RETENTION_POLICY_YAML)


def sources_yaml(sources_block: str) -> str:
    """Composes a minimal valid sources.yaml around the given sources block."""
    return (
        "schema_version: 1\n"
        "schedule_classes:\n"
        "  daily:\n"
        "    target_interval_minutes: 1440\n"
        "    description: Daily\n"
        "sanitization_profiles:\n"
        "  default_html_article:\n"
        "    input_preference: [summary]\n"
        "    decode_entities: true\n"
        "    remove_selectors: [script]\n"
        "sources:\n"
        + sources_block
    )


_SOURCE_TEMPLATE = """  - id: {id}
    title: {title}
    xml_url: {xml_url}
    category_id: 1
    fetch_group: {fetch_group}
    schedule_class: {schedule_class}
    sanitization_profile: default_html_article
    enabled: {enabled}
"""


def source_block(**overrides) -> str:
    values = {
        "id": 101,
        "title": "Test Feed",
        "xml_url": "https://example.com/rss",
        "fetch_group": 1,
        "schedule_class": "daily",
        "enabled": "true",
    }
    values.update(overrides)
    return _SOURCE_TEMPLATE.format(**values)


def write_sources(config_dir: pathlib.Path, sources: str) -> None:
    """Writes sources.yaml wrapping the given sources block."""
    with open(config_dir / "sources.yaml", "w", encoding="utf-8") as f:
        f.write(sources_yaml(sources))
