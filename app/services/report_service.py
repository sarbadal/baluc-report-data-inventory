from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from io import BytesIO
import re
from zoneinfo import ZoneInfo
from app.services.storage import StorageProvider


IST = ZoneInfo("Asia/Kolkata")


@dataclass
class StorageFileSummary:
    path: str
    category: str
    size_bytes: int
    size_label: str
    uploaded_at: str


@dataclass
class StorageFolderSummary:
    name: str
    path: str
    total_files: int
    last_uploaded_at: str
    folders: list["StorageFolderSummary"]
    files: list[StorageFileSummary]


@dataclass
class StorageCategorySummary:
    category: str
    total_files: int
    total_size_bytes: int
    total_size_label: str
    min_file_date: str
    max_file_date: str
    missing_file_dates_count: int
    missing_file_dates: list[str]
    last_uploaded_at: str


@dataclass
class StorageInventorySummary:
    generated_at: str
    total_files: int
    total_categories: int
    total_size_bytes: int
    total_size_label: str
    last_uploaded_at: str
    categories: list[StorageCategorySummary]
    recent_files: list[StorageFileSummary]
    root_files: list[StorageFileSummary]
    folder_tree: list[StorageFolderSummary]
    error: str | None = None


@dataclass
class _CategoryStats:
    count: int = 0
    size: int = 0
    earliest_file_date: date | None = None
    latest_file_date: date | None = None
    file_dates: set[date] = field(default_factory=set)
    latest_uploaded_at: datetime | None = None


@dataclass
class _FolderNodeBuilder:
    name: str
    path: str
    folders: dict[str, "_FolderNodeBuilder"] = field(default_factory=dict)
    files: list[StorageFileSummary] = field(default_factory=list)
    latest_uploaded_at: datetime | None = None


class ReportService:
    _INVENTORY_STATE_PATH = "_inventory_state/recent_uploads_cleared_at.json"
    _INTERNAL_PREFIXES = ("_upload_jobs/", "_upload_chunks/", "_inventory_state/")

    def __init__(self, storage_provider: StorageProvider) -> None:
        self.storage_provider = storage_provider

    def download_storage_file(self, path: str) -> bytes:
        return self.storage_provider.download(path)

    def get_storage_inventory_summary(self) -> StorageInventorySummary:
        generated_at = self._format_dt(datetime.now(IST))
        recent_clear_cursor = self._load_recent_clear_cursor()
        try:
            objects = self.storage_provider.list_file_objects(prefix="")
        except Exception as exc:
            return self._build_empty_summary(
                generated_at=generated_at,
                error=f"Unable to read storage inventory: {exc}",
            )

        if not objects:
            return self._build_empty_summary(generated_at=generated_at)

        category_stats: dict[str, _CategoryStats] = {}
        recent_rows: list[tuple[datetime, StorageFileSummary]] = []
        all_rows: list[tuple[datetime, StorageFileSummary]] = []
        total_size = 0
        latest_uploaded: datetime | None = None

        for obj in objects:
            if not obj.path or obj.path.endswith("/"):
                continue
            if self._is_internal_path(obj.path):
                continue

            category = obj.path.split("/", 1)[0] or "uncategorized"
            file_size = int(obj.size_bytes or 0)
            uploaded_at = self._resolve_uploaded_at(obj.updated_at)
            file_date = self._resolve_file_date(obj.path)

            stats = category_stats.setdefault(category, _CategoryStats())
            stats.count += 1
            stats.size += file_size
            if file_date is not None:
                stats.file_dates.add(file_date)
                if stats.earliest_file_date is None or file_date < stats.earliest_file_date:
                    stats.earliest_file_date = file_date
                if stats.latest_file_date is None or file_date > stats.latest_file_date:
                    stats.latest_file_date = file_date
            if stats.latest_uploaded_at is None or uploaded_at > stats.latest_uploaded_at:
                stats.latest_uploaded_at = uploaded_at

            if latest_uploaded is None or uploaded_at > latest_uploaded:
                latest_uploaded = uploaded_at

            total_size += file_size

            file_summary = self._build_file_summary(
                path=obj.path,
                category=category,
                size_bytes=file_size,
                uploaded_at=uploaded_at,
            )
            recent_rows.append((uploaded_at, file_summary))
            all_rows.append((uploaded_at, file_summary))

        categories = self._build_category_summaries(category_stats)
        categories.sort(key=lambda row: (-row.total_files, row.category.lower()))

        if recent_clear_cursor is not None:
            recent_rows = [
                row
                for row in recent_rows
                if row[0] > recent_clear_cursor
            ]

        recent_rows.sort(key=lambda item: item[0], reverse=True)
        recent_files = [row for _, row in recent_rows[:15]]
        all_rows.sort(key=lambda item: item[0], reverse=True)
        root_files, folder_tree = self._build_folder_tree(all_rows)

        return StorageInventorySummary(
            generated_at=generated_at,
            total_files=sum(row.total_files for row in categories),
            total_categories=len(categories),
            total_size_bytes=total_size,
            total_size_label=self._humanize_size(total_size),
            last_uploaded_at=self._format_dt(latest_uploaded),
            categories=categories,
            recent_files=recent_files,
            root_files=root_files,
            folder_tree=folder_tree,
        )

    def clear_recent_uploads(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "cleared_at": now.isoformat(),
        }
        self.storage_provider.upload(
            stream=BytesIO(json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")),
            destination_path=self._INVENTORY_STATE_PATH,
            content_type="application/json",
        )

    def _build_empty_summary(self, generated_at: str, error: str | None = None) -> StorageInventorySummary:
        return StorageInventorySummary(
            generated_at=generated_at,
            total_files=0,
            total_categories=0,
            total_size_bytes=0,
            total_size_label=self._humanize_size(0),
            last_uploaded_at="-",
            categories=[],
            recent_files=[],
            root_files=[],
            folder_tree=[],
            error=error,
        )

    def _build_folder_tree(
        self,
        rows: list[tuple[datetime, StorageFileSummary]],
    ) -> tuple[list[StorageFileSummary], list[StorageFolderSummary]]:
        root = _FolderNodeBuilder(name="", path="")

        for uploaded_at, file_summary in rows:
            if root.latest_uploaded_at is None or uploaded_at > root.latest_uploaded_at:
                root.latest_uploaded_at = uploaded_at

            parts = [part for part in file_summary.path.split("/") if part]
            if len(parts) <= 1:
                root.files.append(file_summary)
                continue

            node = root
            for index, folder_name in enumerate(parts[:-1]):
                folder_path = "/".join(parts[: index + 1])
                child = node.folders.get(folder_name)
                if child is None:
                    child = _FolderNodeBuilder(name=folder_name, path=folder_path)
                    node.folders[folder_name] = child
                if child.latest_uploaded_at is None or uploaded_at > child.latest_uploaded_at:
                    child.latest_uploaded_at = uploaded_at
                node = child

            node.files.append(file_summary)

        def materialize(node: _FolderNodeBuilder) -> StorageFolderSummary:
            folders = [materialize(child) for _, child in sorted(node.folders.items(), key=lambda item: item[0].lower())]
            files = sorted(node.files, key=lambda item: item.path.lower())
            total_files = len(files) + sum(folder.total_files for folder in folders)
            return StorageFolderSummary(
                name=node.name,
                path=node.path,
                total_files=total_files,
                last_uploaded_at=self._format_dt(node.latest_uploaded_at),
                folders=folders,
                files=files,
            )

        root_files = sorted(root.files, key=lambda item: item.path.lower())
        folder_tree = [materialize(child) for _, child in sorted(root.folders.items(), key=lambda item: item[0].lower())]
        return root_files, folder_tree

    @staticmethod
    def _resolve_uploaded_at(value: datetime | None) -> datetime:
        return value or datetime.min.replace(tzinfo=timezone.utc)

    def _load_recent_clear_cursor(self) -> datetime | None:
        try:
            raw = self.storage_provider.download(self._INVENTORY_STATE_PATH)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        cleared_at = payload.get("cleared_at")
        if not isinstance(cleared_at, str) or not cleared_at.strip():
            return None
        try:
            parsed = datetime.fromisoformat(cleared_at)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @classmethod
    def _is_internal_path(cls, path: str) -> bool:
        normalized = str(path).strip()
        return any(normalized.startswith(prefix) for prefix in cls._INTERNAL_PREFIXES)

    def _build_file_summary(self, path: str, category: str, size_bytes: int, uploaded_at: datetime) -> StorageFileSummary:
        return StorageFileSummary(
            path=path,
            category=category,
            size_bytes=size_bytes,
            size_label=self._humanize_size(size_bytes),
            uploaded_at=self._format_dt(uploaded_at),
        )

    def _build_category_summaries(self, category_stats: dict[str, _CategoryStats]) -> list[StorageCategorySummary]:
        summaries: list[StorageCategorySummary] = []
        for name, stats in category_stats.items():
            missing_dates = self._missing_dates_between(
                start=stats.earliest_file_date,
                end=stats.latest_file_date,
                present_dates=stats.file_dates,
            )
            summaries.append(
                StorageCategorySummary(
                    category=name,
                    total_files=stats.count,
                    total_size_bytes=stats.size,
                    total_size_label=self._humanize_size(stats.size),
                    min_file_date=self._format_file_date(stats.earliest_file_date),
                    max_file_date=self._format_file_date(stats.latest_file_date),
                    missing_file_dates_count=len(missing_dates),
                    missing_file_dates=[item.strftime("%Y-%m-%d") for item in missing_dates],
                    last_uploaded_at=self._format_dt(stats.latest_uploaded_at),
                )
            )
        return summaries

    @staticmethod
    def _missing_dates_between(start: date | None, end: date | None, present_dates: set[date]) -> list[date]:
        if start is None or end is None or start >= end:
            return []

        missing: list[date] = []
        # Include leading missing dates from the 1st day of the min-date month.
        current = start.replace(day=1)
        while current <= end:
            if current not in present_dates:
                missing.append(current)
            current = current.fromordinal(current.toordinal() + 1)

        return missing

    @staticmethod
    def _resolve_file_date(path: str) -> date | None:
        filename = path.rsplit("/", 1)[-1]
        candidates = [filename, path]

        iso_pattern = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")
        compact_pattern = re.compile(r"(?<!\d)(\d{8})(?!\d)")

        for candidate in candidates:
            match = iso_pattern.search(candidate)
            if match:
                year, month, day = match.groups()
                try:
                    return date(int(year), int(month), int(day))
                except ValueError:
                    pass

            match = compact_pattern.search(candidate)
            if match:
                value = match.group(1)
                try:
                    return datetime.strptime(value, "%Y%m%d").date()
                except ValueError:
                    pass

        return None

    @staticmethod
    def _humanize_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 ** 3:
            return f"{size_bytes / (1024 ** 2):.1f} MB"
        return f"{size_bytes / (1024 ** 3):.1f} GB"

    @staticmethod
    def _format_dt(value: datetime | None) -> str:
        if value is None:
            return "-"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_file_date(value: date | None) -> str:
        if value is None:
            return "-"
        return value.strftime("%Y-%m-%d")

