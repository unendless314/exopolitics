import pathlib
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field, field_validator

class DatabaseSettings(BaseModel):
    busy_timeout_ms: int = 10000

class ReportingDefaults(BaseModel):
    days: int = 7
    format: str = "markdown"
    output_dir: str = "reports/analysis/"
    stdout: bool = False
    log_path: Optional[str] = None
    maturation_offset_hours: int = 2

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("markdown", "json"):
            raise ValueError("format must be 'markdown' or 'json'")
        return v

    @field_validator("maturation_offset_hours")
    @classmethod
    def validate_maturation_offset_hours(cls, v: int) -> int:
        if v < 0:
            raise ValueError("maturation_offset_hours must be at least 0")
        return v

class ReportingSettings(BaseModel):
    defaults: ReportingDefaults

class ThresholdsSettings(BaseModel):
    overall_yield: float = 0.10
    relevance_rate: float = 0.40

class SafeguardsSettings(BaseModel):
    fetch_success_rate_isolation: float = 0.50

class QuadrantClassifierSettings(BaseModel):
    thresholds: ThresholdsSettings
    safeguards: SafeguardsSettings

class AnalysisSettings(BaseModel):
    schema_version: int
    database: DatabaseSettings
    reporting: ReportingSettings
    quadrant_classifier: QuadrantClassifierSettings

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported schema_version: {v}")
        return v

def load_analysis_settings(config_path: pathlib.Path) -> AnalysisSettings:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return AnalysisSettings(**data)


# Metadata resolution helpers for external configuration files
class SourceMeta(BaseModel):
    id: int
    title: str
    xml_url: str
    category_id: int
    enabled: bool
    fetch_group: int
    schedule_class: str
    html_url: Optional[str] = None

class CategoryMeta(BaseModel):
    name: str
    slug: str
    enabled: bool

def load_sources_config(sources_yaml_path: pathlib.Path) -> Dict[int, SourceMeta]:
    """
    Loads and parses sources.yaml configuration.
    """
    if not sources_yaml_path.exists():
        return {}
    with open(sources_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    
    sources_data = data.get("sources", [])
    result = {}
    for src in sources_data:
        try:
            meta = SourceMeta(**src)
            result[meta.id] = meta
        except Exception:
            # Skip invalid source entries, allowing robust execution
            continue
    return result

def load_categories_config(categories_yaml_path: pathlib.Path) -> Dict[int, CategoryMeta]:
    """
    Loads and parses categories.yaml configuration.
    """
    if not categories_yaml_path.exists():
        return {}
    with open(categories_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    
    categories_data = data.get("categories", {})
    result = {}
    for cat_id_str, cat in categories_data.items():
        try:
            cat_id = int(cat_id_str)
            meta = CategoryMeta(**cat)
            result[cat_id] = meta
        except Exception:
            continue
    return result
