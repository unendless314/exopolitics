import pathlib
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import yaml

@dataclass(frozen=True)
class RequestDefaults:
    temperature: float
    top_p: float
    max_output_tokens: int

@dataclass(frozen=True)
class ExecutionPolicy:
    batch_size: int
    max_concurrent_requests: int
    rate_limit_per_minute: int
    request_timeout_seconds: float
    min_context_characters: int
    retry_attempts: int
    backoff_factor: float

@dataclass(frozen=True)
class ProviderConfig:
    api_type: str
    api_key_env: str
    model_name: str
    supports_structured_output: bool
    api_base: Optional[str] = None

@dataclass(frozen=True)
class DeterministicClassification:
    model_name: str
    prompt_version: str

@dataclass(frozen=True)
class ClassifyConfig:
    active_provider: str
    active_prompt_template: str
    request_defaults: RequestDefaults
    execution_policy: ExecutionPolicy
    providers: Dict[str, ProviderConfig]
    deterministic_classification: DeterministicClassification

def load_yaml(file_path: pathlib.Path) -> Any:
    """Helper to safely load a YAML file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_classify_config(config_path: pathlib.Path) -> ClassifyConfig:
    """
    Loads classification settings from the specified model_settings.yaml path.
    """
    raw_config = load_yaml(config_path) or {}

    # Parse request defaults
    defaults_raw = raw_config.get("request_defaults", {})
    request_defaults = RequestDefaults(
        temperature=float(defaults_raw.get("temperature", 0.1)),
        top_p=float(defaults_raw.get("top_p", 0.95)),
        max_output_tokens=int(defaults_raw.get("max_output_tokens", 1024))
    )

    # Parse execution policy
    policy_raw = raw_config.get("execution_policy", {})
    execution_policy = ExecutionPolicy(
        batch_size=int(policy_raw.get("batch_size", 20)),
        max_concurrent_requests=int(policy_raw.get("max_concurrent_requests", 3)),
        rate_limit_per_minute=int(policy_raw.get("rate_limit_per_minute", 60)),
        request_timeout_seconds=float(policy_raw.get("request_timeout_seconds", 10.0)),
        min_context_characters=int(policy_raw.get("min_context_characters", 100)),
        retry_attempts=int(policy_raw.get("retry_attempts", 3)),
        backoff_factor=float(policy_raw.get("backoff_factor", 2.0))
    )

    # Parse providers
    providers_raw = raw_config.get("providers", {})
    providers = {}
    for name, p_data in providers_raw.items():
        if isinstance(p_data, dict):
            providers[name] = ProviderConfig(
                api_type=p_data.get("api_type", ""),
                api_key_env=p_data.get("api_key_env", ""),
                model_name=p_data.get("model_name", ""),
                supports_structured_output=bool(p_data.get("supports_structured_output", False)),
                api_base=p_data.get("api_base")
            )

    # Parse deterministic classification settings
    determ_raw = raw_config.get("deterministic_classification", {})
    deterministic_classification = DeterministicClassification(
        model_name=determ_raw.get("model_name", "deterministic-low-context"),
        prompt_version=determ_raw.get("prompt_version", "rule_v1")
    )

    return ClassifyConfig(
        active_provider=raw_config.get("active_provider", ""),
        active_prompt_template=raw_config.get("active_prompt_template", ""),
        request_defaults=request_defaults,
        execution_policy=execution_policy,
        providers=providers,
        deterministic_classification=deterministic_classification
    )
