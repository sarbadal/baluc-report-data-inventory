from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from io import BytesIO
import os
from pathlib import Path
from typing import BinaryIO

from flask import Config

try:
    from google.cloud import storage as gcs_storage
except ImportError:  # pragma: no cover
    gcs_storage = None

try:
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover
    service_account = None


class StorageProvider(ABC):
    @abstractmethod
    def upload(self, stream: BinaryIO, destination_path: str, content_type: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_file_objects(self, prefix: str) -> list[StorageObject]:
        raise NotImplementedError

    @abstractmethod
    def download(self, path: str) -> bytes:
        raise NotImplementedError


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(self, stream: BinaryIO, destination_path: str, content_type: str) -> str:
        del content_type
        target_path = self.root / destination_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(stream.read())
        return str(target_path)

    def list_file_objects(self, prefix: str) -> list[StorageObject]:
        base = self.root / prefix
        if base.is_file():
            stat = base.stat()
            return [
                StorageObject(
                    path=str(base.relative_to(self.root)),
                    size_bytes=stat.st_size,
                    updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            ]
        if not base.exists():
            return []
        return [
            StorageObject(
                path=str(path.relative_to(self.root)),
                size_bytes=path.stat().st_size,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            )
            for path in base.rglob("*")
            if path.is_file()
        ]

    def download(self, path: str) -> bytes:
        target_path = self.root / path
        return target_path.read_bytes()


class GCSStorageProvider(StorageProvider):
    def __init__(self, bucket_name: str) -> None:
        if gcs_storage is None:
            raise RuntimeError("google-cloud-storage is not installed")
        if not bucket_name:
            raise ValueError("GCS bucket name is required")

        self.bucket_name = bucket_name
        credentials, project_id = self._build_credentials_from_env()
        if credentials is not None:
            self.client = gcs_storage.Client(credentials=credentials, project=project_id)
        else:
            self.client = gcs_storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    @staticmethod
    def _build_credentials_from_env():
        raw_json = os.getenv("GOOGLE_AUTH_KEY_JSON", "").strip()
        if not raw_json:
            return None, None
        if service_account is None:
            raise RuntimeError("google-auth is not installed")

        # Handle values wrapped in matching quotes from .env files.
        if len(raw_json) >= 2 and raw_json[0] == raw_json[-1] and raw_json[0] in {'"', "'"}:
            raw_json = raw_json[1:-1]

        try:
            info = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError("GOOGLE_AUTH_KEY_JSON is not valid JSON") from exc

        credentials = service_account.Credentials.from_service_account_info(info)
        project_id = info.get("project_id") if isinstance(info, dict) else None
        return credentials, project_id

    def upload(self, stream: BinaryIO, destination_path: str, content_type: str) -> str:
        blob = self.bucket.blob(destination_path)
        blob.upload_from_file(stream, content_type=content_type)
        return f"gs://{self.bucket_name}/{destination_path}"

    def list_file_objects(self, prefix: str) -> list[StorageObject]:
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        return [
            StorageObject(
                path=blob.name,
                size_bytes=getattr(blob, "size", None),
                updated_at=getattr(blob, "updated", None),
            )
            for blob in blobs
            if blob.name
        ]

    def download(self, path: str) -> bytes:
        blob = self.bucket.blob(path)
        buffer = BytesIO()
        blob.download_to_file(buffer)
        return buffer.getvalue()


def build_storage_provider(config: Config) -> StorageProvider:
    backend = str(config.get("STORAGE_BACKEND", "local")).strip().lower()
    has_inline_gcs_key = bool(os.getenv("GOOGLE_AUTH_KEY_JSON", "").strip())

    # If inline service-account JSON is provided, prefer GCS automatically.
    if has_inline_gcs_key:
        backend = "gcs"

    if backend == "gcs":
        return GCSStorageProvider(config.get("GCS_BUCKET_NAME", ""))

    return LocalStorageProvider(Path(config.get("LOCAL_STORAGE_ROOT", "storage")))


@dataclass
class StorageObject:
    path: str
    size_bytes: int | None = None
    updated_at: datetime | None = None
