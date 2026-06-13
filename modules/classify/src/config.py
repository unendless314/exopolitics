import pathlib
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, field_validator

class RequestDefaults(BaseModel):
    temperature: float = 0.1
    top_p: float = 0.95
    max_output_tokens: int = 1024

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 2.0):
            raise ValueError(f"temperature must be between 0.0 and 2.0, got {v}")
        return v

    @field_validator("top_p")
    @classmethod
    def validate_top_p(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"top_p must be between 0.0 and 1.0, got {v}")
        return v

class ExecutionPolicy(BaseModel):
    batch_size: int = 20
    max_concurrent_requests: int = 3
    rate_limit_per_minute: int = 60
    request_timeout_seconds: float = 45.0
    retry_attempts: int = 3
    backoff_factor: float = 2.0

    @field_validator("batch_size", "max_concurrent_requests", "rate_limit_per_minute", "retry_attempts")
    @classmethod
    def validate_positive_ints(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Value must be a positive integer")
        return v

class ProviderConfig(BaseModel):
    api_type: str
    api_key_env: str
    model_name: str
    supports_structured_output: bool = False
    api_base: Optional[str] = None

class DeterministicClassification(BaseModel):
    model_name: str = "deterministic-low-context"
    prompt_version: str = "rule_v1"

class ModelSettingsYaml(BaseModel):
    active_provider: str
    active_prompt_template: str
    request_defaults: RequestDefaults
    execution_policy: ExecutionPolicy
    providers: Dict[str, ProviderConfig]
    deterministic_classification: DeterministicClassification

    @field_validator("providers")
    @classmethod
    def validate_active_provider_exists(cls, v: Dict[str, ProviderConfig], info) -> Dict[str, ProviderConfig]:
        active = info.data.get("active_provider")
        if active and active not in v:
            raise ValueError(f"Active provider '{active}' is not defined in providers registry.")
        return v

class TemplateConfig(BaseModel):
    version: str
    description: Optional[str] = None
    system_instruction: str
    user_prompt_template: str

class PromptTemplatesYaml(BaseModel):
    templates: Dict[str, TemplateConfig]

class ClassifyConfig:
    def __init__(self, settings: ModelSettingsYaml, templates: PromptTemplatesYaml):
        self.settings = settings
        self.templates = templates

        self.active_provider_name = settings.active_provider
        self.active_provider = settings.providers[settings.active_provider]
        self.active_template_name = settings.active_prompt_template
        self.active_template = templates.templates[settings.active_prompt_template]
        self.execution_policy = settings.execution_policy
        self.request_defaults = settings.request_defaults
        self.deterministic = settings.deterministic_classification


def load_yaml_file(path: pathlib.Path) -> Any:
    """Helper to load a YAML file safely."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_and_load_config(config_dir: pathlib.Path) -> ClassifyConfig:
    """
    Loads all config files from the config directory, parses them, 
    and validates schemas. Throws exception on error.
    """
    settings_path = config_dir / "model_settings.yaml"
    templates_path = config_dir / "prompt_templates.yaml"

    if not settings_path.exists():
        raise FileNotFoundError(f"Missing configuration file: model_settings.yaml at {settings_path}")
    if not templates_path.exists():
        raise FileNotFoundError(f"Missing configuration file: prompt_templates.yaml at {templates_path}")

    # Parse settings
    settings_raw = load_yaml_file(settings_path)
    if settings_raw is None or not isinstance(settings_raw, dict):
        raise ValueError("model_settings.yaml is invalid: must be a mapping")
    settings = ModelSettingsYaml(**settings_raw)

    # Parse templates
    templates_raw = load_yaml_file(templates_path)
    if templates_raw is None or not isinstance(templates_raw, dict):
        raise ValueError("prompt_templates.yaml is invalid: must be a mapping")
    templates = PromptTemplatesYaml(**templates_raw)

    # Validate active prompt template exists
    if settings.active_prompt_template not in templates.templates:
        raise ValueError(f"Active prompt template '{settings.active_prompt_template}' is not defined in templates.")

    return ClassifyConfig(settings, templates)
