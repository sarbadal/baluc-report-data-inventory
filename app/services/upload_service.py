from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
import re
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from werkzeug.datastructures import FileStorage

from app.services.category_detection import CategoryDetectionService
from app.services.naming import FilenameConventionService
from app.services.storage import GCSStorageProvider
from app.services.upload_processors import UploadFrame, UploadProcessor
from app.services.storage import StorageProvider
from app.utils.dates import year_month_path


@dataclass
class UploadResult:
    success: bool
    requires_category: bool
    message: str
    original_filename: str
    category: str | None = None
    file_type: str = "fact"
    destination_path: str | None = None
    storage_uri: str | None = None
    uploaded_paths: list[str] | None = None
    uploaded_uris: list[str] | None = None


@dataclass
class UploadRequest:
    file: FileStorage
    chosen_category: str | None = None
    file_type: str | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass
class UploadProcessorRequest:
    df: pd.DataFrame
    category: str
    file_type: str
    original_filename: str


@dataclass
class UploadService:
    naming_service: FilenameConventionService
    category_detection_service: CategoryDetectionService
    storage_provider: StorageProvider
    processing_category_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    category_filename_rule_configs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    mapping_file_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    fact_key_filter_categories: set[str] = field(default_factory=lambda: {"contact", "ev", "print"})
    upload_base_prefix: str = ""
    upload_processors: list[UploadProcessor] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.upload_base_prefix = self.upload_base_prefix.strip("/")
        self.fact_key_filter_categories = {
            str(category).strip()
            for category in self.fact_key_filter_categories
            if str(category).strip()
        }

    def handle_upload(self, request: UploadRequest) -> UploadResult:
        executor = _UploadExecution(
            service=self,
            request=request,
        )
        return executor.run()

    def handle_bulk_upload(
        self,
        files: list[FileStorage],
        chosen_categories: list[str],
        file_types: list[str],
    ) -> list[UploadResult]:
        results: list[UploadResult] = []

        for idx, file in enumerate(files):
            chosen_category = chosen_categories[idx] if idx < len(chosen_categories) else ""
            normalized_category = chosen_category.strip() or None
            chosen_file_type = file_types[idx] if idx < len(file_types) else "fact"
            normalized_file_type = self._normalize_file_type(chosen_file_type)
            if normalized_file_type is None and normalized_category:
                normalized_file_type = self.naming_service.default_file_type_for_category(normalized_category)
            if normalized_file_type is None:
                normalized_file_type = "fact"
            result = self.handle_upload(
                UploadRequest(
                    file=file,
                    chosen_category=normalized_category,
                    file_type=normalized_file_type,
                )
            )
            results.append(result)

        return results

    def _build_destination_path(
        self,
        *,
        category: str,
        frame: UploadFrame,
        original_filename: str,
    ) -> str:
        category_config = self.processing_category_configs.get(category, {})
        storage_options = category_config.get("storage_options", {}) if isinstance(category_config, dict) else {}

        path_mode = "category_year_month"
        if isinstance(storage_options, dict):
            path_mode = str(storage_options.get("path_mode", "category_year_month")).strip().lower()

        target_filename = self._resolve_output_filename(
            category=category,
            frame=frame,
            original_filename=original_filename,
            storage_options=storage_options,
        )

        if path_mode == "category_only":
            relative_path = f"{category}/{target_filename}"
        else:
            relative_path = f"{category}/{year_month_path(frame.file_date)}/{target_filename}"

        if self.upload_base_prefix:
            return f"{self.upload_base_prefix}/{relative_path}"
        return relative_path

    def _resolve_output_filename(
        self,
        *,
        category: str,
        frame: UploadFrame,
        original_filename: str,
        storage_options: Any,
    ) -> str:
        default_name = self.naming_service.build_filename(category, frame.file_date)
        if not isinstance(storage_options, dict):
            return default_name

        merged_rules: list[dict[str, Any]] = []
        inline_rules = storage_options.get("filename_rules", [])
        if isinstance(inline_rules, list):
            merged_rules.extend(rule for rule in inline_rules if isinstance(rule, dict))

        external_rules = self.category_filename_rule_configs.get(category, [])
        if isinstance(external_rules, list):
            merged_rules.extend(rule for rule in external_rules if isinstance(rule, dict))

        for rule in merged_rules:
            output_filename = self._normalize_output_filename(rule.get("output_filename"))
            if output_filename is None:
                continue

            patterns = rule.get("match_patterns", [])
            if not isinstance(patterns, list):
                continue
            if any(self._safe_pattern_match(pattern, original_filename) for pattern in patterns):
                return output_filename

        use_original_filename = bool(storage_options.get("use_original_filename", False))
        if use_original_filename:
            normalized_original = self._normalize_output_filename(original_filename)
            if normalized_original is not None:
                return normalized_original

        default_output_filename = self._normalize_output_filename(storage_options.get("default_output_filename"))
        if default_output_filename is not None:
            return default_output_filename

        return default_name

    @staticmethod
    def _normalize_output_filename(raw_filename: Any) -> str | None:
        if not isinstance(raw_filename, str):
            return None
        candidate = Path(raw_filename).name.strip()
        if not candidate:
            return None
        if not candidate.lower().endswith(".csv"):
            candidate = f"{candidate}.csv"
        return candidate

    @staticmethod
    def _safe_pattern_match(pattern: Any, filename: str) -> bool:
        if not isinstance(pattern, str) or not pattern.strip():
            return False
        try:
            return re.search(pattern, filename, flags=re.IGNORECASE) is not None
        except re.error:
            return False

    def _run_upload_processors(self, request: UploadProcessorRequest) -> list[UploadFrame]:
        frames = [UploadFrame(df=request.df, file_date=date.today())]

        if request.file_type == "dim":
            return frames

        for processor in self.upload_processors:
            next_frames: list[UploadFrame] = []
            for frame in frames:
                outputs = processor.process(
                    frame,
                    category=request.category,
                    original_filename=request.original_filename,
                )
                next_frames.extend(outputs)
            frames = next_frames

        frames = self._enforce_fact_category_output_columns(
            frames=frames,
            category=request.category,
        )

        return frames

    def _enforce_fact_category_output_columns(
        self,
        *,
        frames: list[UploadFrame],
        category: str,
    ) -> list[UploadFrame]:
        if category not in self.fact_key_filter_categories:
            return frames

        category_config = self.processing_category_configs.get(category, {})
        field_mapping = category_config.get("field_mapping", {}) if isinstance(category_config, dict) else {}
        if not isinstance(field_mapping, dict) or not field_mapping:
            return frames

        target_columns = [
            target.strip()
            for target in field_mapping.keys()
            if isinstance(target, str) and target.strip()
        ]
        if not target_columns:
            return frames

        output_frames: list[UploadFrame] = []
        for frame in frames:
            working_df = frame.df.copy()

            source_to_target = {
                source.strip(): target.strip()
                for target, source in field_mapping.items()
                if isinstance(target, str)
                and target.strip()
                and isinstance(source, str)
                and source.strip()
                and target.strip() not in working_df.columns
                and source.strip() in working_df.columns
            }
            if source_to_target:
                working_df = working_df.rename(columns=source_to_target)

            columns_to_keep = [column for column in target_columns if column in working_df.columns]
            if columns_to_keep:
                filtered_df = working_df.loc[:, columns_to_keep].copy()
            else:
                filtered_df = working_df

            output_frames.append(UploadFrame(df=filtered_df, file_date=frame.file_date))

        return output_frames

    def has_mapping_file_configs(self) -> bool:
        return bool(self.mapping_file_configs)

    def resolve_mapping_upload_target(self, config: dict[str, Any]) -> tuple[str | None, str | None]:
        raw_gcs_path = str(config.get("gcs_path", "")).strip()
        if not raw_gcs_path:
            return None, "Mapping configuration is missing gcs_path."

        if raw_gcs_path.startswith("gs://"):
            parsed = self._parse_gcs_uri(raw_gcs_path)
            if parsed is None:
                return None, f"Invalid gcs_path in mapping configuration: '{raw_gcs_path}'."

            bucket_name, object_path = parsed
            if isinstance(self.storage_provider, GCSStorageProvider):
                if bucket_name != self.storage_provider.bucket_name:
                    return (
                        None,
                        "Configured storage bucket does not match mapping gcs_path bucket "
                        f"('{bucket_name}').",
                    )
            return object_path, None

        normalized_path = raw_gcs_path.lstrip("/")
        if not normalized_path:
            return None, "Mapping gcs_path must include a non-empty object path."
        return normalized_path, None

    def match_mapping_file(self, df: pd.DataFrame) -> tuple[dict[str, Any] | None, str | None]:
        if not self.mapping_file_configs:
            return None, "No mapping definitions were found in app/resources/processing/mapping."

        valid_matches: list[dict[str, Any]] = []
        rejection_reasons: list[str] = []

        for config_name, config in self.mapping_file_configs.items():
            if not isinstance(config, dict):
                continue

            is_valid, score, reason = self._is_valid_mapping_config_for_dataframe(df, config)
            if is_valid:
                valid_matches.append(
                    {
                        "config_name": config_name,
                        "config": config,
                        "score": score,
                    }
                )
            elif reason:
                rejection_reasons.append(f"{config_name}: {reason}")

        if not valid_matches:
            details = "; ".join(rejection_reasons[:3]) if rejection_reasons else "No mapping rule could be evaluated."
            return None, (
                "Uploaded CSV does not match any mapping definition in app/resources/processing/mapping. "
                f"Details: {details}"
            )

        valid_matches.sort(key=lambda item: float(item["score"]), reverse=True)
        if len(valid_matches) > 1:
            top_score = float(valid_matches[0]["score"])
            second_score = float(valid_matches[1]["score"])
            if abs(top_score - second_score) <= 0.02:
                return None, (
                    "Uploaded CSV matches multiple mapping definitions with similar confidence. "
                    "Please make the file content more specific."
                )

        return valid_matches[0], None

    def _is_valid_mapping_config_for_dataframe(
        self,
        df: pd.DataFrame,
        config: dict[str, Any],
    ) -> tuple[bool, float, str | None]:
        hints = config.get("content_hints")
        if not isinstance(hints, dict):
            return False, 0.0, "content_hints is missing or invalid"

        pattern_rules = hints.get("column_value_patterns")
        if not isinstance(pattern_rules, list) or not pattern_rules:
            return False, 0.0, "content_hints.column_value_patterns must be a non-empty list"

        field_mapping = config.get("field_mapping")
        if not isinstance(field_mapping, dict) or not field_mapping:
            return False, 0.0, "field_mapping is missing or invalid"

        normalized_to_actual = {
            str(column).strip().lower(): str(column)
            for column in df.columns
        }

        for source_column in field_mapping.values():
            if not isinstance(source_column, str) or not source_column.strip():
                return False, 0.0, "field_mapping contains empty source column names"
            if source_column.strip().lower() not in normalized_to_actual:
                return False, 0.0, f"required mapped column '{source_column}' is missing"

        raw_sample_size = hints.get("sample_size", 50)
        try:
            sample_size = max(1, int(raw_sample_size))
        except (TypeError, ValueError):
            sample_size = 50

        raw_default_min_ratio = hints.get("min_match_ratio", 0.9)
        try:
            default_min_ratio = float(raw_default_min_ratio)
        except (TypeError, ValueError):
            default_min_ratio = 0.9
        default_min_ratio = min(max(default_min_ratio, 0.0), 1.0)

        weighted_total = 0.0
        weighted_match = 0.0

        for rule in pattern_rules:
            if not isinstance(rule, dict):
                return False, 0.0, "column_value_patterns contains a non-object rule"

            raw_column = rule.get("column")
            raw_pattern = rule.get("pattern")
            if not isinstance(raw_column, str) or not raw_column.strip():
                return False, 0.0, "a column_value_patterns rule has empty column"
            if not isinstance(raw_pattern, str) or not raw_pattern.strip():
                return False, 0.0, "a column_value_patterns rule has empty pattern"

            actual_column = normalized_to_actual.get(raw_column.strip().lower())
            if actual_column is None:
                return False, 0.0, f"required hint column '{raw_column}' is missing"

            try:
                compiled_pattern = re.compile(raw_pattern, re.IGNORECASE)
            except re.error:
                return False, 0.0, f"invalid regex in content_hints for column '{raw_column}'"

            try:
                weight = float(rule.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            if weight <= 0:
                continue

            raw_rule_min_ratio = rule.get("min_match_ratio", default_min_ratio)
            try:
                rule_min_ratio = float(raw_rule_min_ratio)
            except (TypeError, ValueError):
                rule_min_ratio = default_min_ratio
            rule_min_ratio = min(max(rule_min_ratio, 0.0), 1.0)

            series = df[actual_column].dropna().astype(str).head(sample_size)
            if series.empty:
                return False, 0.0, f"column '{actual_column}' has no sample values"

            match_ratio = float(series.str.contains(compiled_pattern, na=False).mean())
            if match_ratio < rule_min_ratio:
                return (
                    False,
                    match_ratio,
                    f"column '{actual_column}' pattern match ratio {match_ratio:.2f} is below required {rule_min_ratio:.2f}",
                )

            weighted_total += weight
            weighted_match += match_ratio * weight

        if weighted_total <= 0:
            return False, 0.0, "all content_hints rules have non-positive weights"

        return True, weighted_match / weighted_total, None

    @staticmethod
    def _parse_gcs_uri(raw_uri: str) -> tuple[str, str] | None:
        match = re.match(r"^gs://([^/]+)/(.+)$", raw_uri.strip())
        if match is None:
            return None
        bucket_name = match.group(1).strip()
        object_path = match.group(2).strip().lstrip("/")
        if not bucket_name or not object_path:
            return None
        return bucket_name, object_path

    @staticmethod
    def _normalize_file_type(raw_file_type: str | None) -> str | None:
        normalized = str(raw_file_type or "").strip().lower()
        if not normalized:
            return None
        if normalized not in {"fact", "dim"}:
            return None
        return normalized


@dataclass
class _UploadExecution:
    service: UploadService
    request: UploadRequest
    filename: str = ""
    category: str | None = None
    file_type: str = "fact"

    def run(self) -> UploadResult:
        self.filename = (self.request.file.filename or "").strip()
        self.file_type = self.service._normalize_file_type(self.request.file_type) or ""

        filename_error = self._validate_filename()
        if filename_error is not None:
            return self._error_result(message=filename_error)

        df, read_error = self._read_csv()
        if read_error is not None:
            return self._error_result(message=read_error)

        assert df is not None

        category_error, requires_category = self._resolve_category(df)
        if category_error is not None:
            return self._error_result(
                message=category_error,
                requires_category=requires_category,
            )

        assert self.category is not None
        if not self.file_type:
            self.file_type = self.service.naming_service.default_file_type_for_category(self.category)
        if self.file_type not in {"fact", "dim"}:
            self.file_type = "fact"

        if self.category == "mapping":
            return self._handle_mapping_upload(df)

        frames, process_error = self._process_frames(df=df)
        if process_error is not None:
            return self._error_result(
                message=process_error,
                category=self.category,
            )

        if not frames:
            return self._error_result(
                message="Upload processors returned no output data.",
                category=self.category,
            )

        self._emit_progress(
            {
                "event": "frames_ready",
                "filename": self.filename,
                "category": self.category,
                "total_outputs": len(frames),
            }
        )

        uploaded_paths, uploaded_uris = self._upload_frames(frames)

        return UploadResult(
            success=True,
            requires_category=False,
            message=(
                f"File processed into {len(uploaded_paths)} output file(s) and uploaded successfully."
            ),
            original_filename=self.filename,
            category=self.category,
            file_type=self.file_type,
            destination_path=uploaded_paths[0] if uploaded_paths else None,
            storage_uri=uploaded_uris[0] if uploaded_uris else None,
            uploaded_paths=uploaded_paths,
            uploaded_uris=uploaded_uris,
        )

    def _validate_filename(self) -> str | None:
        if not self.filename:
            return "Please choose a CSV file."
        if not self.filename.lower().endswith(".csv"):
            return "Only CSV files are supported."
        return None

    def _resolve_category(self, df: pd.DataFrame) -> tuple[str | None, bool]:
        normalized_chosen = (self.request.chosen_category or "").strip() or None
        if normalized_chosen and not self.service.naming_service.has_category(normalized_chosen):
            return "Invalid file category selected.", True

        filename_detected_category = self.service.naming_service.detect_category(self.filename)

        if normalized_chosen:
            if filename_detected_category and filename_detected_category != normalized_chosen:
                return (
                    f"Selected category '{normalized_chosen}' conflicts with filename convention "
                    f"('{filename_detected_category}'). Please choose the correct category.",
                    True,
                )

            category_scores: dict[str, float] = {}
            for category in self.service.naming_service.categories():
                score = self.service.category_detection_service.category_match_score(category, df)
                if score is not None:
                    category_scores[category] = score

            selected_score = category_scores.get(normalized_chosen)
            if selected_score is None:
                if normalized_chosen == "mapping" and self.service.has_mapping_file_configs():
                    self.category = normalized_chosen
                    return None, False
                return (
                    f"Selected category '{normalized_chosen}' has no detection configuration. "
                    "Please contact admin or choose another category.",
                    True,
                )

            if selected_score < self.service.category_detection_service.min_confidence:
                return (
                    f"Selected category '{normalized_chosen}' does not match CSV content "
                    f"(match score {selected_score:.2f}). Please choose the correct category.",
                    True,
                )

            best_category = max(category_scores, key=category_scores.get)
            best_score = category_scores[best_category]
            if best_category != normalized_chosen and best_score > selected_score:
                return (
                    f"Selected category '{normalized_chosen}' does not match CSV content. "
                    f"Best detected category is '{best_category}' (score {best_score:.2f} vs {selected_score:.2f}).",
                    True,
                )

            self.category = normalized_chosen
            return None, False

        detected = self.service.category_detection_service.detect_category(
            filename=self.filename,
            df=df,
        )
        detected_category = detected.category if detected is not None else None

        if not detected_category and not normalized_chosen:
            return (
                "Unable to auto-detect file category from filename/CSV fields. Please select a file category.",
                True,
            )

        self.category = detected_category or normalized_chosen
        if not self.category or not self.service.naming_service.has_category(self.category):
            return "Invalid file category selected.", True

        return None, False

    def _read_csv(self) -> tuple[pd.DataFrame | None, str | None]:
        try:
            self.request.file.stream.seek(0)
            return pd.read_csv(self.request.file.stream), None
        except Exception as exc:
            return None, f"Unable to read CSV content: {exc}"

    def _process_frames(self, df: pd.DataFrame) -> tuple[list[UploadFrame], str | None]:
        assert self.category is not None
        try:
            return self.service._run_upload_processors(
                UploadProcessorRequest(
                    df=df,
                    category=self.category,
                    file_type=self.file_type,
                    original_filename=self.filename,
                )
            ), None
        except ValueError as exc:
            return [], str(exc)

    def _upload_frames(self, frames: list[UploadFrame]) -> tuple[list[str], list[str]]:
        assert self.category is not None

        uploaded_paths: list[str] = []
        uploaded_uris: list[str] = []

        for idx, frame in enumerate(frames, start=1):
            destination_path = self.service._build_destination_path(
                category=self.category,
                frame=frame,
                original_filename=self.filename,
            )

            self._emit_progress(
                {
                    "event": "uploading_output",
                    "filename": self.filename,
                    "category": self.category,
                    "output_index": idx,
                    "total_outputs": len(frames),
                    "destination_path": destination_path,
                }
            )

            payload = frame.df.to_csv(index=False).encode("utf-8")
            storage_uri = self.service.storage_provider.upload(
                stream=BytesIO(payload),
                destination_path=destination_path,
                content_type="text/csv",
            )

            uploaded_paths.append(destination_path)
            uploaded_uris.append(storage_uri)

            self._emit_progress(
                {
                    "event": "uploaded_output",
                    "filename": self.filename,
                    "category": self.category,
                    "output_index": idx,
                    "total_outputs": len(frames),
                    "destination_path": destination_path,
                }
            )

        return uploaded_paths, uploaded_uris

    def _handle_mapping_upload(self, df: pd.DataFrame) -> UploadResult:
        mapping_match, mapping_error = self.service.match_mapping_file(df)
        if mapping_error is not None or mapping_match is None:
            return self._error_result(
                message=mapping_error or "Unable to determine mapping definition.",
                category=self.category,
            )

        mapping_config = mapping_match["config"]
        destination_path, destination_error = self.service.resolve_mapping_upload_target(mapping_config)
        if destination_error is not None or destination_path is None:
            return self._error_result(
                message=destination_error or "Unable to resolve mapping upload destination.",
                category=self.category,
            )

        self._emit_progress(
            {
                "event": "uploading_output",
                "filename": self.filename,
                "category": self.category,
                "output_index": 1,
                "total_outputs": 1,
                "destination_path": destination_path,
            }
        )

        payload = df.to_csv(index=False).encode("utf-8")
        storage_uri = self.service.storage_provider.upload(
            stream=BytesIO(payload),
            destination_path=destination_path,
            content_type="text/csv",
        )

        self._emit_progress(
            {
                "event": "uploaded_output",
                "filename": self.filename,
                "category": self.category,
                "output_index": 1,
                "total_outputs": 1,
                "destination_path": destination_path,
            }
        )

        matched_name = str(mapping_match.get("config_name", "unknown"))
        return UploadResult(
            success=True,
            requires_category=False,
            message=(
                f"Mapping file validated against '{matched_name}' and uploaded successfully."
            ),
            original_filename=self.filename,
            category=self.category,
            file_type="dim",
            destination_path=destination_path,
            storage_uri=storage_uri,
            uploaded_paths=[destination_path],
            uploaded_uris=[storage_uri],
        )

    def _emit_progress(self, payload: dict[str, Any]) -> None:
        if self.request.progress_callback is not None:
            self.request.progress_callback(payload)

    def _error_result(
        self,
        message: str,
        requires_category: bool = False,
        category: str | None = None,
    ) -> UploadResult:
        return UploadResult(
            success=False,
            requires_category=requires_category,
            message=message,
            original_filename=self.filename,
            category=category,
            file_type=self.file_type,
        )
