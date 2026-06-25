import pathlib
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, field_validator

class ExecutionPolicy(BaseModel):
    default_export_dir: str = "data/publish_export"
    batch_size: int = 1000

    @field_validator("batch_size")
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("batch_size must be a positive integer greater than zero")
        return v

class IndexPolicy(BaseModel):
    latest_limit: int = 1000
    archive_granularity: str = "month"

    @field_validator("latest_limit")
    @classmethod
    def validate_latest_limit(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("latest_limit must be a positive integer greater than zero")
        return v

    @field_validator("archive_granularity")
    @classmethod
    def validate_archive_granularity(cls, v: str) -> str:
        if v != "month":
            raise ValueError("archive_granularity must equal 'month'")
        return v

class PublishSettingsYaml(BaseModel):
    target_languages: Dict[str, str]
    coverage_policy: str = "strict_match"
    execution_policy: ExecutionPolicy
    index_policy: IndexPolicy

    @field_validator("target_languages")
    @classmethod
    def validate_target_languages(cls, v: Dict[str, str]) -> Dict[str, str]:
        if not v:
            raise ValueError("target_languages must contain a non-empty dictionary of language mappings")
        return v

    @field_validator("coverage_policy")
    @classmethod
    def validate_coverage_policy(cls, v: str) -> str:
        if v != "strict_match":
            raise ValueError("coverage_policy must be 'strict_match'")
        return v


class PublishConfig:
    def __init__(self, settings: PublishSettingsYaml):
        self.settings = settings
        self.target_languages = settings.target_languages
        self.coverage_policy = settings.coverage_policy
        self.execution_policy = settings.execution_policy
        self.index_policy = settings.index_policy


def load_yaml_file(path: pathlib.Path) -> Any:
    """Helper to load a YAML file safely."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_and_load_config(config_path: pathlib.Path) -> PublishConfig:
    """
    Loads the config file from the specified path, parses it,
    and validates schemas. Throws exception on error.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Missing configuration file: {config_path}")

    raw = load_yaml_file(config_path)
    if raw is None or not isinstance(raw, dict):
        raise ValueError(f"{config_path.name} is invalid: must be a mapping")

    settings = PublishSettingsYaml(**raw)
    return PublishConfig(settings)
