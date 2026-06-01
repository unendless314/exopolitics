import pathlib
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import yaml

@dataclass(frozen=True)
class CategoryConfig:
    id: Any
    name: Any
    enabled: Any
    slug: Any = None

@dataclass(frozen=True)
class ScheduleClassConfig:
    name: Any
    target_interval_minutes: Any
    description: Any

@dataclass(frozen=True)
class SourceConfig:
    id: Any
    title: Any
    xml_url: Any
    category_id: Any
    fetch_group: Any
    schedule_class: Any
    enabled: Any
    html_url: Any = None
    notes: Any = None

@dataclass(frozen=True)
class IngestConfig:
    categories: Dict[Any, CategoryConfig]
    schedule_classes: Dict[Any, ScheduleClassConfig]
    sources: List[SourceConfig]

def load_yaml(file_path: pathlib.Path) -> Any:
    """Helper to safely load a YAML file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_config(config_dir: pathlib.Path) -> IngestConfig:
    """
    Loads categories and sources configurations from the specified directory.
    Preserves all raw YAML scalar types to allow the validator to enforce strict schema constraints.
    """
    categories_path = config_dir / "categories.yaml"
    sources_path = config_dir / "sources.yaml"

    categories_raw = load_yaml(categories_path) or {}
    sources_raw = load_yaml(sources_path) or {}

    # Parse Categories (Preserve raw types, do not coerce keys/values)
    categories = {}
    categories_dict = categories_raw.get("categories", {})
    if isinstance(categories_dict, dict):
        for cat_id, cat_data in categories_dict.items():
            if isinstance(cat_data, dict):
                categories[cat_id] = CategoryConfig(
                    id=cat_id,
                    name=cat_data.get("name"),
                    enabled=cat_data.get("enabled"),
                    slug=cat_data.get("slug")
                )

    # Parse Schedule Classes (Preserve raw types)
    schedule_classes = {}
    sched_classes_raw = sources_raw.get("schedule_classes", {})
    if isinstance(sched_classes_raw, dict):
        for name, data in sched_classes_raw.items():
            if isinstance(data, dict):
                schedule_classes[name] = ScheduleClassConfig(
                    name=name,
                    target_interval_minutes=data.get("target_interval_minutes"),
                    description=data.get("description")
                )

    # Parse Sources (Preserve raw types, do not coerce to int or bool)
    sources = []
    sources_list = sources_raw.get("sources", [])
    if isinstance(sources_list, list):
        for s in sources_list:
            if isinstance(s, dict):
                sources.append(
                    SourceConfig(
                        id=s.get("id"),
                        title=s.get("title"),
                        xml_url=s.get("xml_url"),
                        category_id=s.get("category_id"),
                        fetch_group=s.get("fetch_group"),
                        schedule_class=s.get("schedule_class"),
                        enabled=s.get("enabled"),
                        html_url=s.get("html_url"),
                        notes=s.get("notes")
                    )
                )

    return IngestConfig(
        categories=categories,
        schedule_classes=schedule_classes,
        sources=sources
    )
