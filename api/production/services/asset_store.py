"""The engine's content-addressed file store.

A step hashes everything that goes into its output; `find` returns an
earlier result for the same hash, so the step is skipped and nothing is paid
twice. Files live in object storage under `production/<course>/<hash>`.
"""

import hashlib
import json
from pathlib import Path

from api.production.models import ProductionAsset
from shared.services.storage_service import StorageService

EXTENSIONS = {
    "audio/mpeg": "mp3",
    "image/png": "png",
    "video/mp4": "mp4",
    "application/json": "json",
    "application/zip": "zip",
    "text/vtt": "vtt",
    "application/x-subrip": "srt",
    "text/plain": "txt",
}


def input_hash(*parts) -> str:
    """SHA-256 of the step's inputs, in a stable encoding."""

    encoded = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def find(key: str) -> ProductionAsset | None:
    return ProductionAsset.objects.filter(key=key).first()


def put(path: Path, *, course_id, name: str, content_type: str) -> str:
    """Store a local file; returns its key in storage."""

    file_key = f"production/{course_id}/{name}.{EXTENSIONS[content_type]}"
    return StorageService.upload_file(path, file_key=file_key, content_type=content_type)


def put_bytes(data: bytes, *, course_id, name: str, content_type: str, workdir: Path) -> str:
    path = workdir / f"{name}.{EXTENSIONS[content_type]}"
    path.write_bytes(data)
    return put(path, course_id=course_id, name=name, content_type=content_type)


def fetch(file_key: str, dest: Path) -> Path:
    StorageService.download_file(file_key, dest)
    return dest


def record(
    *,
    key: str,
    kind: str,
    course,
    file_key: str,
    content_type: str,
    path: Path | None = None,
    lesson=None,
    duration_ms: int | None = None,
    data: dict | None = None,
) -> ProductionAsset:
    asset, _ = ProductionAsset.objects.update_or_create(
        key=key,
        defaults={
            "kind": kind,
            "course": course,
            "lesson": lesson,
            "file_key": file_key,
            "content_type": content_type,
            "size_bytes": path.stat().st_size if path is not None else 0,
            "duration_ms": duration_ms,
            "data": data or {},
        },
    )
    return asset


def public_url(file_key: str) -> str:
    return StorageService.public_url(file_key)
