import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import sqlite3
import pytest
import jsonschema
from modules.analysis.tests.generate_mock_db import DDL_STATEMENTS

def load_schema():
    path = pathlib.Path(__file__).parent.parent / "docs" / "REPORT_CONTRACTS.md"
    content = path.read_text(encoding="utf-8")
    match = re.search(r"### 2\.1 JSON Top-Level Structure\s*\n```json\n(.*?)\n```", content, re.DOTALL)
    if not match:
        raise ValueError("Could not find JSON schema in REPORT_CONTRACTS.md")
    return json.loads(match.group(1))

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()

    # Seed minimal data
    now = "2026-07-15T12:00:00Z"

    # source_state
    conn.execute("""
        INSERT INTO source_state (source_id, health_status, consecutive_failures, updated_at)
        VALUES (1, 'healthy', 0, ?)
    """, (now,))

    # source_item
    conn.execute("""
        INSERT INTO source_item (source_item_id, source_id, title, published_at, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (301, 1, "Sighting Title", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-301", "guid"))

    # source_item_text
    conn.execute("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "Body content", "default", 0, 0, "completed", None, 100, 100, now, now))

    # fetch_run
    conn.execute("""
        INSERT INTO fetch_run (fetch_run_id, started_at, run_scope, trigger_type, run_status, due_source_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (1, "2026-07-10T09:59:50Z", "all", "cron", "completed", 1))

    # fetch_attempt
    conn.execute("""
        INSERT INTO fetch_attempt (fetch_run_id, source_id, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?)
    """, (1, 1, "2026-07-10T09:59:50Z", "2026-07-10T10:00:00Z", "success"))

    # classification_result
    conn.execute("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_confidence, content_density, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "core", 0.95, "high", "test-model", "v1.0", "2026-07-10T10:10:00Z", now))

    # curation_decision
    conn.execute("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "approved", None, "looks good", "operator", "test-model", "v1.0", "2026-07-10T10:30:00Z", now, now))

    # approved_content_record
    conn.execute("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, content_body, content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (501, 301, "Approved Display Title", "Body content", "fp-301", "en", "2026-07-10T10:30:00Z", now, now))

    # translation_output
    conn.executemany("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, content, source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (501, 301, "zh", "中文標題", "中文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", now),
        (501, 301, "ja", "日文標題", "日文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", now)
    ])

    # publish_record
    conn.execute("""
        INSERT INTO publish_record (
            publish_record_id, source_item_id, slug, first_published_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (701, 301, "slug-301", "2026-07-10T11:15:00Z", now, now))

    # publish_language_status
    conn.execute("""
        INSERT INTO publish_language_status (
            publish_record_id, language_code, publish_status, published_at, source_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (701, "zh", "published", "2026-07-10T11:15:00Z", "fp-301", now))

    conn.commit()
    conn.close()

    yield path
    try:
        os.remove(path)
    except Exception:
        pass

def run_cli_command(db_path, command_args):
    # Ensure PYTHONPATH is set so modules can be imported
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pathlib.Path(__file__).parent.parent.parent.parent.resolve())

    full_args = [
        sys.executable, "-m", "modules.analysis.src.cli",
        *command_args,
        "--db-path", str(db_path)
    ]
    res = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        env=env
    )
    return res

def test_json_schema_validation_endpoints(temp_db):
    schema = load_schema()

    # 1. analyze-funnel
    res = run_cli_command(temp_db, ["analyze-funnel", "--format", "json", "--stdout"])
    assert res.returncode == 0, f"Error: {res.stderr}"
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)
    # funnel 3.0.0: low-context observation fields replace the bypass fields
    assert data["schema_version"] == "3.0.0"
    assert "low_context_observation_count" in data["raw_metrics"]
    assert "low_context_observation_count" in data["raw_metrics"]["classification_readiness_breakdown"]
    assert "low_context_bypass_count" not in data["raw_metrics"]
    assert "low_context_bypass" not in data["raw_metrics"]["classification_readiness_breakdown"]

    # 2. analyze-classify
    res = run_cli_command(temp_db, ["analyze-classify", "--format", "json", "--stdout"])
    assert res.returncode == 0, f"Error: {res.stderr}"
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)

    # 3. analyze-sources
    res = run_cli_command(temp_db, ["analyze-sources", "--format", "json", "--stdout"])
    assert res.returncode == 0, f"Error: {res.stderr}"
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)
    # sources 2.0.0: renamed low-context observation rate
    assert data["schema_version"] == "2.0.0"
    assert "low_context_observation_rate" in data["metrics"]
    assert "low_context_bypass_rate" not in data["metrics"]

    # 4. analyze-translation
    res = run_cli_command(temp_db, ["analyze-translation", "--format", "json", "--stdout"])
    assert res.returncode == 0, f"Error: {res.stderr}"
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)

    # 5. analyze-curation
    res = run_cli_command(temp_db, ["analyze-curation", "--format", "json", "--stdout"])
    assert res.returncode == 0, f"Error: {res.stderr}"
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)

def test_markdown_and_stdout_outputs(temp_db):
    # Verify we can output markdown format
    res = run_cli_command(temp_db, ["analyze-funnel", "--format", "markdown", "--stdout"])
    assert res.returncode == 0
    assert "# Pipeline Funnel Conversion & Bottleneck Report" in res.stdout
    assert "Latency metrics include system initialization/historical ingestion data" in res.stdout

    res = run_cli_command(temp_db, ["analyze-curation", "--format", "markdown", "--stdout"])
    assert res.returncode == 0
    assert "# Curation Performance & Diagnostics Report" in res.stdout

def test_cli_output_files_writing(temp_db):
    with tempfile.TemporaryDirectory() as temp_dir:
        res = run_cli_command(temp_db, ["analyze-funnel", "--format", "json", "--output-dir", temp_dir])
        assert res.returncode == 0
        report_file = pathlib.Path(temp_dir) / "PIPELINE_FUNNEL_REPORT.json"
        assert report_file.exists()

        # Verify schema
        schema = load_schema()
        data = json.loads(report_file.read_text(encoding="utf-8"))
        jsonschema.validate(instance=data, schema=schema)

def test_mock_ddl_constraints_violation(empty_db_conn):
    # Status CHECK constraint violation
    with pytest.raises(sqlite3.IntegrityError):
        empty_db_conn.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected,
                was_truncated, text_processing_status, text_processing_reason,
                raw_text_length, sanitized_text_length, created_at, updated_at
            ) VALUES (999, "Body", "default", 0, 0, "invalid_status_value", NULL, 10, 10, "now", "now")
        """)

    # Reason CHECK constraint violation
    with pytest.raises(sqlite3.IntegrityError):
        empty_db_conn.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected,
                was_truncated, text_processing_status, text_processing_reason,
                raw_text_length, sanitized_text_length, created_at, updated_at
            ) VALUES (999, "Body", "default", 0, 0, "completed", "invalid_reason_value", 10, 10, "now", "now")
        """)

def test_empty_db_endpoints(temp_db):
    # Clear out all database tables to test zero records edge case
    conn = sqlite3.connect(str(temp_db))
    conn.execute("DELETE FROM publish_language_status;")
    conn.execute("DELETE FROM publish_record;")
    conn.execute("DELETE FROM translation_output;")
    conn.execute("DELETE FROM approved_content_record;")
    conn.execute("DELETE FROM curation_decision;")
    conn.execute("DELETE FROM classification_result;")
    conn.execute("DELETE FROM source_item_text;")
    conn.execute("DELETE FROM source_item;")
    conn.commit()
    conn.close()

    schema = load_schema()

    # funnel
    res = run_cli_command(temp_db, ["analyze-funnel", "--format", "json", "--stdout"])
    assert res.returncode == 0
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)
    assert data["raw_metrics"]["total_ingested"] == 0

    # classify
    res = run_cli_command(temp_db, ["analyze-classify", "--format", "json", "--stdout"])
    assert res.returncode == 0
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)

    # curation
    res = run_cli_command(temp_db, ["analyze-curation", "--format", "json", "--stdout"])
    assert res.returncode == 0
    data = json.loads(res.stdout)
    jsonschema.validate(instance=data, schema=schema)

def test_negative_maturation_offset_rejection(temp_db):
    res = run_cli_command(temp_db, ["analyze-funnel", "--maturation-offset-hours", "-1"])
    assert res.returncode != 0
    assert "maturation offset hours must be at least 0" in res.stderr

    from modules.analysis.src.config import ReportingDefaults
    with pytest.raises(ValueError):
        ReportingDefaults(maturation_offset_hours=-5)
