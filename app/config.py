from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _bootstrap_env() -> None:
    if load_dotenv is None:
        return
    project_root = Path(__file__).resolve().parent.parent
    env_file = project_root / ".env"
    load_dotenv(dotenv_path=env_file, override=False)


_bootstrap_env()


def _resolve_max_content_length() -> int:
    raw_bytes = os.getenv("MAX_UPLOAD_SIZE_BYTES", "").strip()
    if raw_bytes:
        try:
            return int(raw_bytes)
        except ValueError:
            pass

    raw_mb = os.getenv("MAX_UPLOAD_SIZE_MB", "200").strip()
    try:
        size_mb = int(raw_mb)
    except ValueError:
        size_mb = 200
    return size_mb * 1024 * 1024


def _parse_csv_env_list(raw_value: str, default: list[str]) -> list[str]:
    parsed = [item.strip() for item in raw_value.split(",") if item.strip()]
    return parsed or default


def _normalize_app_env(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if value in {"prod", "production"}:
        return "prod"
    if value in {"dev", "development"}:
        return "dev"
    return "dev"


def _default_upload_type_rules() -> dict[str, dict]:
    return {
        "contact": {
            "match_patterns": [
                r"^contact_dump_\\d{8}\\.csv$",
                r"^contact_dump.*\\.csv$",
            ],
            "output_stem": "contact_fact",
        },
        "ev": {
            "match_patterns": [
                r"^ev_dump_\\d{8}\\.csv$",
                r"^ev_dump.*\\.csv$",
            ],
            "output_stem": "ev_fact",
        },
    }


def _load_upload_type_rules() -> dict[str, dict]:
    default_rules = _default_upload_type_rules()
    default_path = Path(__file__).resolve().parent / "resources" / "upload_types.json"
    configured_path = Path(os.getenv("UPLOAD_TYPE_RULES_FILE", str(default_path)))

    try:
        raw_rules = json.loads(configured_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw_rules = default_rules

    if not isinstance(raw_rules, dict):
        raw_rules = default_rules

    enabled_categories_raw = os.getenv("UPLOAD_ENABLED_CATEGORIES", "").strip()
    if not enabled_categories_raw:
        return raw_rules

    enabled_categories = [item.strip() for item in enabled_categories_raw.split(",") if item.strip()]
    filtered_rules = {category: raw_rules[category] for category in enabled_categories if category in raw_rules}
    return filtered_rules or raw_rules


def _load_processing_category_configs() -> dict[str, dict]:
    default_dir = Path(__file__).resolve().parent / "resources" / "processing"
    config_dir = Path(os.getenv("PROCESSING_CONFIG_DIR", str(default_dir)))
    configs: dict[str, dict] = {}

    if not config_dir.exists() or not config_dir.is_dir():
        return configs

    for item in config_dir.glob("*.json"):
        try:
            parsed = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            configs[item.stem] = parsed

    return configs


def _load_mapping_storage_rule_configs() -> list[dict]:
    default_dir = Path(__file__).resolve().parent / "resources" / "processing" / "mapping_types"
    config_dir = Path(os.getenv("MAPPING_STORAGE_RULES_DIR", str(default_dir)))
    rules: list[dict] = []

    if not config_dir.exists() or not config_dir.is_dir():
        return rules

    for item in sorted(config_dir.glob("*.json")):
        try:
            parsed = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("_rule_file", item.name)
            rules.append(parsed)

    return rules


def _load_mapping_file_configs() -> dict[str, dict]:
    default_dir = Path(__file__).resolve().parent / "resources" / "processing" / "mapping"
    config_dir = Path(os.getenv("MAPPING_CONFIG_DIR", str(default_dir)))
    configs: dict[str, dict] = {}

    if not config_dir.exists() or not config_dir.is_dir():
        return configs

    for item in sorted(config_dir.glob("*.json")):
        try:
            parsed = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("_config_file", item.name)
            configs[item.stem] = parsed

    return configs


def validate_processing_category_configs(
    upload_type_rules: dict[str, dict],
    processing_category_configs: dict[str, dict],
    mapping_file_configs: dict[str, dict] | None = None,
) -> None:
    mapping_file_configs = mapping_file_configs or {}
    enabled_categories = list(upload_type_rules.keys())
    missing_categories = [
        category
        for category in enabled_categories
        if category not in processing_category_configs
        and not (category == "mapping" and bool(mapping_file_configs))
    ]
    if missing_categories:
        missing = ", ".join(sorted(missing_categories))
        raise ValueError(
            "Missing processing configuration JSON for categories: "
            f"{missing}. Add <category>.json in PROCESSING_CONFIG_DIR."
        )

    errors: list[str] = []
    for category in enabled_categories:
        config = processing_category_configs.get(category)
        if category == "mapping" and config is None and mapping_file_configs:
            continue
        if not isinstance(config, dict):
            errors.append(f"{category}: config must be a JSON object")
            continue

        category_rule = upload_type_rules.get(category, {})
        default_file_type = str(category_rule.get("default_file_type", "fact")).strip().lower()
        requires_split_date_column = default_file_type != "dim"

        if requires_split_date_column:
            split_date_column = config.get("split_date_column")
            if not isinstance(split_date_column, str) or not split_date_column.strip():
                errors.append(f"{category}: split_date_column must be a non-empty string")

        field_mapping = config.get("field_mapping")
        if not isinstance(field_mapping, dict) or not field_mapping:
            errors.append(f"{category}: field_mapping must be a non-empty object")
        else:
            invalid_mapping = [
                key
                for key, value in field_mapping.items()
                if not isinstance(key, str)
                or not key.strip()
                or not isinstance(value, str)
                or not value.strip()
            ]
            if invalid_mapping:
                errors.append(
                    f"{category}: field_mapping keys/values must be non-empty strings"
                )

    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"Invalid processing category configuration: {joined}")


class Config:
    APP_ENV = _normalize_app_env(os.getenv("APP_ENV", os.getenv("FLASK_ENV", "dev")))
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
    MAX_CONTENT_LENGTH = _resolve_max_content_length()

    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "").strip()
    STATIC_ASSET_BASE_URL = os.getenv("STATIC_ASSET_BASE_URL", "").strip().rstrip("/")
    STATIC_ASSET_VERSION = os.getenv("STATIC_ASSET_VERSION", "").strip()
    STATIC_GCS_PREFIX = os.getenv("STATIC_GCS_PREFIX", "static").strip("/")
    UPLOAD_BASE_PREFIX = os.getenv("UPLOAD_BASE_PREFIX", "").strip("/")
    UPLOAD_PROCESSOR_CLASSES = _parse_csv_env_list(
        os.getenv("UPLOAD_PROCESSOR_CLASSES", "split_daily"),
        ["split_daily"],
    )
    FACT_FIELD_MAPPING_KEY_FILTER_CATEGORIES = _parse_csv_env_list(
        os.getenv("FACT_FIELD_MAPPING_KEY_FILTER_CATEGORIES", "contact,ev,print"),
        ["contact", "ev", "print"],
    )

    LOCAL_STORAGE_ROOT = Path(os.getenv("LOCAL_STORAGE_ROOT", "storage"))

    # Backend-controlled category/naming rules.
    UPLOAD_TYPE_RULES = _load_upload_type_rules()
    PROCESSING_CATEGORY_CONFIGS = _load_processing_category_configs()
    MAPPING_STORAGE_RULE_CONFIGS = _load_mapping_storage_rule_configs()
    MAPPING_FILE_CONFIGS = _load_mapping_file_configs()
