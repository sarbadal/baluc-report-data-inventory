from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import pandas as pd


@dataclass
class UploadFrame:
    df: pd.DataFrame
    file_date: date


class UploadProcessor(ABC):
    @abstractmethod
    def process(self, frame: UploadFrame, *, category: str, original_filename: str) -> list[UploadFrame]:
        raise NotImplementedError


ProcessorFactory = Callable[[dict[str, dict[str, Any]]], UploadProcessor]


@dataclass(frozen=True)
class UploadProcessorSpec:
    name: str
    description: str
    factory: ProcessorFactory


@dataclass
class RenameFieldsUploadProcessor(UploadProcessor):
    category_configs: dict[str, dict[str, Any]]

    def process(self, frame: UploadFrame, *, category: str, original_filename: str) -> list[UploadFrame]:
        del original_filename
        category_config = self.category_configs.get(category, {})
        field_mapping = category_config.get("field_mapping", {})
        if not isinstance(field_mapping, dict) or not field_mapping:
            return [frame]

        source_to_target = self._resolve_source_to_target_mapping(frame.df, field_mapping)
        if not source_to_target:
            return [frame]

        renamed_df = frame.df.rename(columns=source_to_target)
        return [UploadFrame(df=renamed_df, file_date=frame.file_date)]

    @staticmethod
    def _resolve_source_to_target_mapping(df: pd.DataFrame, field_mapping: dict[str, Any]) -> dict[str, str]:
        direct_mapping = {
            source: target
            for source, target in field_mapping.items()
            if isinstance(source, str)
            and source in df.columns
            and isinstance(target, str)
            and target.strip()
        }

        reverse_mapping = {
            source: target
            for target, source in field_mapping.items()
            if isinstance(target, str)
            and target.strip()
            and isinstance(source, str)
            and source in df.columns
        }

        if len(direct_mapping) >= len(reverse_mapping):
            return direct_mapping
        return reverse_mapping


@dataclass
class SplitDailyUploadProcessor(UploadProcessor):
    category_configs: dict[str, dict[str, Any]]

    def process(self, frame: UploadFrame, *, category: str, original_filename: str) -> list[UploadFrame]:
        del original_filename
        category_config = self.category_configs.get(category, {})
        split_date_column = str(category_config.get("split_date_column", "")).strip()
        if not split_date_column:
            raise ValueError(f"Missing split_date_column configuration for category '{category}'.")

        source_split_column = self._resolve_split_date_column(frame.df, split_date_column, category_config)
        if source_split_column not in frame.df.columns:
            raise ValueError(
                f"Split date column '{source_split_column}' was not found in uploaded CSV."
            )

        working_df = frame.df.copy()
        working_df[source_split_column] = pd.to_datetime(working_df[source_split_column], errors="coerce")
        dated_rows = working_df[working_df[source_split_column].notna()].copy()
        if dated_rows.empty:
            raise ValueError("No valid date values found for daily split.")

        output_frames: list[UploadFrame] = []
        grouped = dated_rows.groupby(dated_rows[source_split_column].dt.date)
        for split_day, daily_df in grouped:
            output_frames.append(UploadFrame(df=daily_df, file_date=split_day))

        return output_frames

    @staticmethod
    def _resolve_split_date_column(
        df: pd.DataFrame,
        split_date_column: str,
        category_config: dict[str, Any],
    ) -> str:
        if split_date_column in df.columns:
            return split_date_column

        field_mapping = category_config.get("field_mapping", {})
        if not isinstance(field_mapping, dict) or not field_mapping:
            return split_date_column

        direct_match = field_mapping.get(split_date_column)
        if isinstance(direct_match, str) and direct_match.strip():
            return direct_match

        for source_column, target_column in field_mapping.items():
            if isinstance(target_column, str) and target_column == split_date_column:
                if isinstance(source_column, str) and source_column.strip():
                    return source_column

        return split_date_column


@dataclass
class IdentityUploadProcessor(UploadProcessor):
    def process(self, frame: UploadFrame, *, category: str, original_filename: str) -> list[UploadFrame]:
        del category, original_filename
        return [frame]


def _build_identity_processor(_: dict[str, dict[str, Any]]) -> UploadProcessor:
    return IdentityUploadProcessor()


def _build_rename_fields_processor(category_configs: dict[str, dict[str, Any]]) -> UploadProcessor:
    return RenameFieldsUploadProcessor(category_configs=category_configs)


def _build_split_daily_processor(category_configs: dict[str, dict[str, Any]]) -> UploadProcessor:
    return SplitDailyUploadProcessor(category_configs=category_configs)


UPLOAD_PROCESSOR_REGISTRY: dict[str, UploadProcessorSpec] = {
    "identity": UploadProcessorSpec(
        name="identity",
        description="No transformation. Uploads the original dataset as one output file.",
        factory=_build_identity_processor,
    ),
    "rename_fields": UploadProcessorSpec(
        name="rename_fields",
        description="Renames columns using field_mapping from category JSON config.",
        factory=_build_rename_fields_processor,
    ),
    "split_daily": UploadProcessorSpec(
        name="split_daily",
        description="Splits rows into one output file per day using split_date_column from category JSON config.",
        factory=_build_split_daily_processor,
    ),
}


def register_upload_processor(name: str, description: str, factory: ProcessorFactory) -> None:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Upload processor name must be non-empty")
    UPLOAD_PROCESSOR_REGISTRY[normalized_name] = UploadProcessorSpec(
        name=normalized_name,
        description=description.strip() or "No description provided.",
        factory=factory,
    )


def list_upload_processors() -> list[dict[str, str]]:
    return [
        {"name": spec.name, "description": spec.description}
        for spec in sorted(UPLOAD_PROCESSOR_REGISTRY.values(), key=lambda item: item.name)
    ]


def build_upload_processors(
    processor_names: list[str],
    category_configs: dict[str, dict[str, Any]],
) -> list[UploadProcessor]:
    if not processor_names:
        processor_names = ["split_daily"]

    unknown = [name for name in processor_names if name not in UPLOAD_PROCESSOR_REGISTRY]
    if unknown:
        unknown_list = ", ".join(unknown)
        valid_list = ", ".join(sorted(UPLOAD_PROCESSOR_REGISTRY.keys()))
        raise ValueError(f"Unknown upload processor(s): {unknown_list}. Valid values: {valid_list}")

    return [UPLOAD_PROCESSOR_REGISTRY[name].factory(category_configs) for name in processor_names]
