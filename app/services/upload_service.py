from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from typing import Any, Callable

import pandas as pd
from werkzeug.datastructures import FileStorage

from app.services.naming import FilenameConventionService
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
    destination_path: str | None = None
    storage_uri: str | None = None
    uploaded_paths: list[str] | None = None
    uploaded_uris: list[str] | None = None


@dataclass
class UploadRequest:
    file: FileStorage
    chosen_category: str | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass
class UploadProcessorRequest:
    df: pd.DataFrame
    category: str
    original_filename: str


@dataclass
class UploadService:
    naming_service: FilenameConventionService
    storage_provider: StorageProvider
    upload_base_prefix: str = ""
    upload_processors: list[UploadProcessor] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.upload_base_prefix = self.upload_base_prefix.strip("/")

    def handle_upload(self, request: UploadRequest) -> UploadResult:
        executor = _UploadExecution(
            service=self,
            request=request,
        )
        return executor.run()

    def handle_bulk_upload(self, files: list[FileStorage], chosen_categories: list[str]) -> list[UploadResult]:
        results: list[UploadResult] = []

        for idx, file in enumerate(files):
            chosen_category = chosen_categories[idx] if idx < len(chosen_categories) else ""
            normalized_category = chosen_category.strip() or None
            result = self.handle_upload(
                UploadRequest(
                    file=file,
                    chosen_category=normalized_category,
                )
            )
            results.append(result)

        return results

    def _build_destination_path(self, category: str, frame: UploadFrame) -> str:
        target_filename = self.naming_service.build_filename(category, frame.file_date)
        relative_path = f"{category}/{year_month_path(frame.file_date)}/{target_filename}"
        if self.upload_base_prefix:
            return f"{self.upload_base_prefix}/{relative_path}"
        return relative_path

    def _run_upload_processors(self, request: UploadProcessorRequest) -> list[UploadFrame]:
        frames = [UploadFrame(df=request.df, file_date=date.today())]

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

        return frames


@dataclass
class _UploadExecution:
    service: UploadService
    request: UploadRequest
    filename: str = ""
    category: str | None = None

    def run(self) -> UploadResult:
        self.filename = (self.request.file.filename or "").strip()

        filename_error = self._validate_filename()
        if filename_error is not None:
            return self._error_result(message=filename_error)

        category_error, requires_category = self._resolve_category()
        if category_error is not None:
            return self._error_result(
                message=category_error,
                requires_category=requires_category,
            )

        assert self.category is not None

        df, read_error = self._read_csv()
        if read_error is not None:
            return self._error_result(
                message=read_error,
                category=self.category,
            )

        assert df is not None

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

    def _resolve_category(self) -> tuple[str | None, bool]:
        detected_category = self.service.naming_service.detect_category(self.filename)
        normalized_chosen = (self.request.chosen_category or "").strip() or None

        if not detected_category and not normalized_chosen:
            return (
                "Filename does not match existing conventions. Please select a file category.",
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
            destination_path = self.service._build_destination_path(category=self.category, frame=frame)

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
        )
