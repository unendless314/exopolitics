import urllib.parse
from typing import List, Tuple, Set, Any
from .config import IngestConfig

def is_absolute_url(url: str) -> bool:
    """Helper to check if a URL is absolute with a scheme and host."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        return bool(parsed.scheme and parsed.netloc and parsed.scheme in ("http", "https"))
    except Exception:
        return False

def validate_config(config: IngestConfig, max_fetch_groups: int = 8) -> Tuple[List[str], List[str]]:
    """
    Validates IngestConfig against the rules specified in SOURCE_CONFIG_SCHEMA.md.
    Enforces strict type checks on all raw fields loaded from YAML.
    
    Fail Fast Rules (Errors):
    - Duplicate source ID
    - Malformed or non-absolute xml_url
    - Missing category_id reference (must exist and be enabled)
    - Out-of-range fetch_group (1 <= fetch_group <= max_fetch_groups)
    - Unknown schedule_class
    - Invalid type for enabled
    
    Warning Rules (Warnings):
    - Duplicate xml_url across multiple source IDs
    - Missing html_url
    - Suspiciously empty titles
    
    Returns:
        A tuple of (errors: List[str], warnings: List[str])
    """
    errors: List[str] = []
    warnings: List[str] = []

    seen_ids: Set[Any] = set()
    xml_url_to_ids: dict[str, List[Any]] = {}

    # Validate Categories first
    valid_category_ids: Set[Any] = set()
    for cat_id, category in config.categories.items():
        cat_label = f"Category ID {cat_id}"
        
        # Validate category ID type
        if not isinstance(cat_id, int) or isinstance(cat_id, bool):
            errors.append(f"{cat_label}: Category ID must be an integer, got {type(cat_id).__name__}")
            continue

        # Validate name
        if category.name is None:
            errors.append(f"{cat_label}: Missing required 'name' field")
        elif not isinstance(category.name, str) or not category.name.strip():
            errors.append(f"{cat_label}: 'name' must be a non-empty string, got {type(category.name).__name__}")

        # Validate slug
        if category.slug is None:
            errors.append(f"{cat_label}: Missing required 'slug' field")
        elif not isinstance(category.slug, str) or not category.slug.strip():
            errors.append(f"{cat_label}: 'slug' must be a non-empty string, got {type(category.slug).__name__}")

        # Validate category enabled type
        if category.enabled is None:
            errors.append(f"{cat_label}: Missing 'enabled' field")
        elif not isinstance(category.enabled, bool):
            errors.append(f"{cat_label}: 'enabled' must be a boolean, got {type(category.enabled).__name__}")
        elif category.enabled:
            valid_category_ids.add(cat_id)

    # Validate Schedule Classes next
    for name, sc in config.schedule_classes.items():
        sc_label = f"Schedule Class '{name}'"
        if not isinstance(name, str) or not name.strip():
            errors.append(f"Schedule Class name must be a non-empty string, got {type(name).__name__}")
            continue
        
        # Validate target_interval_minutes
        if sc.target_interval_minutes is None:
            errors.append(f"{sc_label}: Missing required 'target_interval_minutes' field")
        elif not isinstance(sc.target_interval_minutes, int) or isinstance(sc.target_interval_minutes, bool):
            errors.append(f"{sc_label}: 'target_interval_minutes' must be an integer, got {type(sc.target_interval_minutes).__name__}")
        elif sc.target_interval_minutes <= 0:
            errors.append(f"{sc_label}: 'target_interval_minutes' must be a positive integer, got {sc.target_interval_minutes}")

        # Validate description
        if sc.description is None:
            errors.append(f"{sc_label}: Missing required 'description' field")
        elif not isinstance(sc.description, str) or not sc.description.strip():
            errors.append(f"{sc_label}: 'description' must be a non-empty string, got {type(sc.description).__name__}")

    # Validate Sources
    for idx, source in enumerate(config.sources):
        source_label = f"Source index {idx}"
        if source.id is not None and isinstance(source.id, int) and not isinstance(source.id, bool):
            source_label = f"Source ID {source.id} ('{source.title or ''}')"

        # 1. Validate ID existence and type (Duplicate source id)
        if source.id is None:
            errors.append(f"{source_label}: Missing unique source ID")
        elif not isinstance(source.id, int) or isinstance(source.id, bool):
            errors.append(f"{source_label}: ID must be an integer, got {type(source.id).__name__}")
        else:
            if source.id in seen_ids:
                errors.append(f"{source_label}: Duplicate source ID {source.id}")
            seen_ids.add(source.id)

        # 2. Malformed or non-absolute xml_url
        if not source.xml_url:
            errors.append(f"{source_label}: xml_url is missing or empty")
        elif not isinstance(source.xml_url, str):
            errors.append(f"{source_label}: xml_url must be a string, got {type(source.xml_url).__name__}")
        elif not is_absolute_url(source.xml_url):
            errors.append(f"{source_label}: xml_url '{source.xml_url}' is malformed or not absolute")
        else:
            # Track duplicate xml_url warnings
            if source.id is not None:
                xml_url_to_ids.setdefault(source.xml_url, []).append(source.id)

        # 3. Missing category_id reference
        if source.category_id is None:
            errors.append(f"{source_label}: category_id is missing")
        elif not isinstance(source.category_id, int) or isinstance(source.category_id, bool):
            errors.append(f"{source_label}: category_id must be an integer, got {type(source.category_id).__name__}")
        elif source.category_id not in config.categories:
            errors.append(f"{source_label}: category_id {source.category_id} does not exist in categories config")
        elif source.category_id not in valid_category_ids:
            category_name = config.categories[source.category_id].name
            errors.append(f"{source_label}: category_id {source.category_id} ('{category_name}') is disabled")

        # 4. Out-of-range fetch_group
        if source.fetch_group is None:
            errors.append(f"{source_label}: fetch_group is missing")
        elif not isinstance(source.fetch_group, int) or isinstance(source.fetch_group, bool):
            errors.append(f"{source_label}: fetch_group must be an integer, got {type(source.fetch_group).__name__}")
        elif source.fetch_group < 1 or source.fetch_group > max_fetch_groups:
            errors.append(f"{source_label}: fetch_group {source.fetch_group} is out of configured range [1, {max_fetch_groups}]")

        # 5. Unknown schedule_class
        if not source.schedule_class:
            errors.append(f"{source_label}: schedule_class is missing")
        elif not isinstance(source.schedule_class, str):
            errors.append(f"{source_label}: schedule_class must be a string, got {type(source.schedule_class).__name__}")
        elif source.schedule_class not in config.schedule_classes:
            errors.append(f"{source_label}: schedule_class '{source.schedule_class}' does not exist in allowed schedule classes")

        # 6. Invalid type for enabled
        if source.enabled is None:
            errors.append(f"{source_label}: enabled field is missing")
        elif not isinstance(source.enabled, bool):
            errors.append(f"{source_label}: enabled must be a boolean, got {type(source.enabled).__name__}")

        # WARNING RULES:
        # A. Missing html_url
        if not source.html_url:
            warnings.append(f"{source_label}: Missing html_url")

        # B. Suspiciously empty titles
        if not source.title or not isinstance(source.title, str) or not source.title.strip():
            warnings.append(f"{source_label}: Suspiciously empty title")

    # C. Duplicate xml_url across multiple source IDs (Warning)
    for url, ids in xml_url_to_ids.items():
        if len(ids) > 1:
            warnings.append(f"Duplicate xml_url '{url}' found across multiple source IDs: {ids}")

    return errors, warnings
