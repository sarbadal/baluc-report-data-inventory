from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
class StorageCategorySummary:
    category: str
    total_files: int
    total_size_bytes: int
    total_size_label: str
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
    error: str | None = None


@dataclass
class _CategoryStats:
    count: int = 0
    size: int = 0
    latest: datetime | None = None


class ReportService:
    def __init__(self, storage_provider: StorageProvider) -> None:
        self.storage_provider = storage_provider

    def download_storage_file(self, path: str) -> bytes:
        return self.storage_provider.download(path)

    def get_storage_inventory_summary(self) -> StorageInventorySummary:
        generated_at = self._format_dt(datetime.now(IST))
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
        total_size = 0
        latest_uploaded: datetime | None = None

        for obj in objects:
            if not obj.path or obj.path.endswith("/"):
                continue

            category = obj.path.split("/", 1)[0] or "uncategorized"
            file_size = int(obj.size_bytes or 0)
            uploaded_at = self._resolve_uploaded_at(obj.updated_at)

            stats = category_stats.setdefault(category, _CategoryStats())
            stats.count += 1
            stats.size += file_size
            if stats.latest is None or uploaded_at > stats.latest:
                stats.latest = uploaded_at

            if latest_uploaded is None or uploaded_at > latest_uploaded:
                latest_uploaded = uploaded_at

            total_size += file_size

            recent_rows.append(
                (
                    uploaded_at,
                    self._build_file_summary(
                        path=obj.path,
                        category=category,
                        size_bytes=file_size,
                        uploaded_at=uploaded_at,
                    ),
                )
            )

        categories = self._build_category_summaries(category_stats)
        categories.sort(key=lambda row: (-row.total_files, row.category.lower()))

        recent_rows.sort(key=lambda item: item[0], reverse=True)
        recent_files = [row for _, row in recent_rows[:15]]

        return StorageInventorySummary(
            generated_at=generated_at,
            total_files=sum(row.total_files for row in categories),
            total_categories=len(categories),
            total_size_bytes=total_size,
            total_size_label=self._humanize_size(total_size),
            last_uploaded_at=self._format_dt(latest_uploaded),
            categories=categories,
            recent_files=recent_files,
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
            error=error,
        )

    @staticmethod
    def _resolve_uploaded_at(value: datetime | None) -> datetime:
        return value or datetime.min.replace(tzinfo=timezone.utc)

    def _build_file_summary(self, path: str, category: str, size_bytes: int, uploaded_at: datetime) -> StorageFileSummary:
        return StorageFileSummary(
            path=path,
            category=category,
            size_bytes=size_bytes,
            size_label=self._humanize_size(size_bytes),
            uploaded_at=self._format_dt(uploaded_at),
        )

    def _build_category_summaries(self, category_stats: dict[str, _CategoryStats]) -> list[StorageCategorySummary]:
        return [
            StorageCategorySummary(
                category=name,
                total_files=stats.count,
                total_size_bytes=stats.size,
                total_size_label=self._humanize_size(stats.size),
                last_uploaded_at=self._format_dt(stats.latest),
            )
            for name, stats in category_stats.items()
        ]

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
