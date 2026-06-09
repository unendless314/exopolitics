import pathlib
import urllib.parse
from typing import Dict, List, Any, Optional, Tuple, Set
import yaml
from pydantic import BaseModel, Field, field_validator

class CategoryConfig(BaseModel):
    name: str
    slug: str
    enabled: bool

class CategoryPolicy(BaseModel):
    purpose: Optional[str] = None
    scheduling_decoupled: Optional[bool] = None

class CategoriesYaml(BaseModel):
    schema_version: int
    category_policy: Optional[CategoryPolicy] = None
    categories: Dict[int, CategoryConfig]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported schema_version: {v}. Only version 1 is supported.")
        return v

    @field_validator("categories")
    @classmethod
    def validate_categories_non_empty(cls, v: Dict[int, CategoryConfig]) -> Dict[int, CategoryConfig]:
        if not v:
            raise ValueError("categories must not be empty")
        return v

class ScheduleClassConfig(BaseModel):
    target_interval_minutes: int
    description: Optional[str] = None

    @field_validator("target_interval_minutes")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("target_interval_minutes must be a positive integer")
        return v

class SanitizationProfile(BaseModel):
    input_preference: List[str] = Field(default_factory=lambda: ["summary", "content"])
    decode_entities: bool = True
    content_selectors: List[str] = Field(default_factory=list)
    remove_selectors: List[str] = Field(default_factory=list)
    normalize_whitespace: bool = True
    collapse_blank_lines: bool = True
    max_length: Optional[int] = None

    @field_validator("max_length")
    @classmethod
    def validate_max_length(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("max_length must be a positive integer")
        return v

    @field_validator("input_preference")
    @classmethod
    def validate_input_preference(cls, v: List[str]) -> List[str]:
        allowed = {"summary", "content", "title"}
        for val in v:
            if val not in allowed:
                raise ValueError(f"input_preference elements must be in {allowed}, got {val}")
        return v

class SourceConfig(BaseModel):
    id: int
    title: str
    xml_url: str
    category_id: int
    enabled: bool
    fetch_group: int
    schedule_class: str
    sanitization_profile: str
    html_url: Optional[str] = None
    notes: Optional[str] = None
    request_headers: Optional[Dict[str, str]] = None
    request_timeout_seconds: Optional[int] = None
    sanitization_overrides: Optional[Dict[str, Any]] = None

    @field_validator("id", "fetch_group")
    @classmethod
    def validate_positive(cls, v: int, info) -> int:
        if v <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v

    @field_validator("title")
    @classmethod
    def validate_non_empty_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must not be empty or whitespace-only")
        return v

    @field_validator("xml_url", "html_url")
    @classmethod
    def validate_urls(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        if not v.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        try:
            parsed = urllib.parse.urlparse(v)
            if not (parsed.scheme and parsed.netloc and parsed.scheme in ("http", "https")):
                raise ValueError(f"{info.field_name} must be a valid absolute HTTP/HTTPS URL")
        except Exception as e:
            raise ValueError(f"{info.field_name} is invalid: {e}")
        return v

    @field_validator("request_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("request_timeout_seconds must be a positive integer")
        return v

    @field_validator("sanitization_overrides")
    @classmethod
    def validate_overrides(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return v
        allowed_keys = set(SanitizationProfile.model_fields.keys())
        for key in v.keys():
            if key not in allowed_keys:
                raise ValueError(f"sanitization_overrides contains invalid field: '{key}'")
        try:
            # Dry-run validation of overrides fields
            SanitizationProfile(**v)
        except Exception as e:
            raise ValueError(f"sanitization_overrides failed validation: {e}")
        return v

class SourcesYaml(BaseModel):
    schema_version: int
    schedule_classes: Dict[str, ScheduleClassConfig]
    sanitization_profiles: Dict[str, SanitizationProfile]
    sources: List[SourceConfig]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported schema_version: {v}. Only version 1 is supported.")
        return v

    @field_validator("schedule_classes")
    @classmethod
    def validate_schedule_classes_non_empty(cls, v: Dict[str, ScheduleClassConfig]) -> Dict[str, ScheduleClassConfig]:
        if not v:
            raise ValueError("schedule_classes must not be empty")
        return v

    @field_validator("sanitization_profiles")
    @classmethod
    def validate_sanitization_profiles_non_empty(cls, v: Dict[str, SanitizationProfile]) -> Dict[str, SanitizationProfile]:
        if not v:
            raise ValueError("sanitization_profiles must not be empty")
        return v

    @field_validator("sources")
    @classmethod
    def validate_sources_non_empty(cls, v: List[SourceConfig]) -> List[SourceConfig]:
        if not v:
            raise ValueError("sources must not be empty")
        return v

class RawRetentionConfig(BaseModel):
    default_days: int
    delete_batch_size: int
    dry_run: bool
    audit_log: bool
    exception_classes: List[str] = Field(default_factory=list)

    @field_validator("default_days", "delete_batch_size")
    @classmethod
    def validate_positive(cls, v: int, info) -> int:
        if v <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v

class RetentionPolicyYaml(BaseModel):
    schema_version: int
    raw_retention: RawRetentionConfig

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported schema_version: {v}. Only version 1 is supported.")
        return v

class IngestConfig:
    def __init__(
        self,
        categories_config: CategoriesYaml,
        sources_config: SourcesYaml,
        retention_config: RetentionPolicyYaml
    ):
        self.categories_yaml = categories_config
        self.sources_yaml = sources_config
        self.retention_yaml = retention_config

        self.categories = categories_config.categories
        self.schedule_classes = sources_config.schedule_classes
        self.sanitization_profiles = sources_config.sanitization_profiles
        self.sources = sources_config.sources
        self.raw_retention = retention_config.raw_retention

    def get_merged_sanitization_profile(self, source: SourceConfig) -> SanitizationProfile:
        """
        Deterministically merge the shared sanitization profile with any source-level overrides.
        Scalar, boolean, and list fields from overrides replace the profile fields.
        """
        profile = self.sanitization_profiles[source.sanitization_profile]
        if not source.sanitization_overrides:
            return profile

        merged_data = profile.model_dump()
        for key, val in source.sanitization_overrides.items():
            if val is not None:
                merged_data[key] = val

        return SanitizationProfile(**merged_data)


def load_yaml_file(path: pathlib.Path) -> Any:
    """Helper to load a YAML file safely."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_and_load_config(config_dir: pathlib.Path) -> Tuple[Optional[IngestConfig], List[str], List[str]]:
    """
    Loads all config files from the config directory, parses them, 
    and validates schemas and cross-file references.
    
    Returns:
        A tuple of (IngestConfig, errors: List[str], warnings: List[str]).
        If there are validation errors, IngestConfig is None.
    """
    errors: List[str] = []
    warnings: List[str] = []

    categories_path = config_dir / "categories.yaml"
    sources_path = config_dir / "sources.yaml"
    retention_path = config_dir / "retention_policy.yaml"

    # Check file existence
    for path, name in [(categories_path, "categories.yaml"), 
                       (sources_path, "sources.yaml"), 
                       (retention_path, "retention_policy.yaml")]:
        if not path.exists():
            errors.append(f"Missing configuration file: {name} at {path}")
    
    if errors:
        return None, errors, warnings

    # Parse Categories
    categories_yaml = None
    try:
        raw = load_yaml_file(categories_path)
        if raw is None or not isinstance(raw, dict):
            errors.append("categories.yaml: Invalid YAML root structure, must be a mapping")
        else:
            categories_yaml = CategoriesYaml(**raw)
    except Exception as e:
        errors.append(f"categories.yaml: Schema validation failed: {e}")

    # Parse Sources
    sources_yaml = None
    try:
        raw = load_yaml_file(sources_path)
        if raw is None or not isinstance(raw, dict):
            errors.append("sources.yaml: Invalid YAML root structure, must be a mapping")
        else:
            sources_yaml = SourcesYaml(**raw)
    except Exception as e:
        errors.append(f"sources.yaml: Schema validation failed: {e}")

    # Parse Retention Policy
    retention_yaml = None
    try:
        raw = load_yaml_file(retention_path)
        if raw is None or not isinstance(raw, dict):
            errors.append("retention_policy.yaml: Invalid YAML root structure, must be a mapping")
        else:
            retention_yaml = RetentionPolicyYaml(**raw)
    except Exception as e:
        errors.append(f"retention_policy.yaml: Schema validation failed: {e}")

    if errors or not categories_yaml or not sources_yaml or not retention_yaml:
        return None, errors, warnings

    # Cross-file reference and domain constraint validation
    # 1. Unique source IDs
    seen_source_ids: Set[int] = set()
    xml_url_to_ids: Dict[str, List[int]] = {}
    html_url_to_ids: Dict[str, List[int]] = {}

    for source in sources_yaml.sources:
        source_label = f"sources.yaml [Source ID {source.id} ('{source.title}')]"

        # ID uniqueness
        if source.id in seen_source_ids:
            errors.append(f"{source_label}: Duplicate source ID {source.id}")
        seen_source_ids.add(source.id)

        # XML URL tracking
        xml_url_to_ids.setdefault(source.xml_url, []).append(source.id)

        # HTML URL tracking
        if source.html_url:
            html_url_to_ids.setdefault(source.html_url, []).append(source.id)

        # Reference Checks
        # Category ID reference
        if source.category_id not in categories_yaml.categories:
            errors.append(f"{source_label}: Referenced category_id {source.category_id} does not exist in categories.yaml")
        else:
            cat = categories_yaml.categories[source.category_id]
            if not cat.enabled:
                errors.append(f"{source_label}: Referenced category_id {source.category_id} ('{cat.name}') is disabled")

        # Schedule class reference
        if source.schedule_class not in sources_yaml.schedule_classes:
            errors.append(f"{source_label}: Referenced schedule_class '{source.schedule_class}' does not exist in schedule_classes")

        # Sanitization profile reference
        if source.sanitization_profile not in sources_yaml.sanitization_profiles:
            errors.append(f"{source_label}: Referenced sanitization_profile '{source.sanitization_profile}' does not exist in sanitization_profiles")

        # Warnings
        # Missing html_url
        if not source.html_url:
            warnings.append(f"{source_label}: Missing html_url")

        # Unusually large selector list warning
        overrides = source.sanitization_overrides or {}
        for key in ["content_selectors", "remove_selectors"]:
            sel_list = overrides.get(key)
            if sel_list and len(sel_list) > 10:
                warnings.append(f"{source_label}: Unusually large selector override list for {key} ({len(sel_list)} items)")

    # 2. Duplicate URL warnings
    for xml_url, ids in xml_url_to_ids.items():
        if len(ids) > 1:
            warnings.append(f"Duplicate xml_url '{xml_url}' found across multiple source IDs: {ids}")

    for html_url, ids in html_url_to_ids.items():
        if len(ids) > 1:
            warnings.append(f"Duplicate html_url '{html_url}' found across multiple source IDs: {ids}")

    if errors:
        return None, errors, warnings

    return IngestConfig(categories_yaml, sources_yaml, retention_yaml), errors, warnings
