from __future__ import annotations

from collections.abc import Iterable

from app.conversion.canonical_field_registry import CanonicalField


def normalize_key(raw_key: str) -> str:
    normalized = (
        str(raw_key or "").strip().lower().replace("’", "'").replace("-", "_").replace(" ", "_")
    )
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def normalize_path(raw_path: str) -> str:
    return ".".join(
        segment
        for segment in (normalize_key(part) for part in str(raw_path or "").split("."))
        if segment
    )


class AliasResolver:
    def __init__(self, fields: Iterable[CanonicalField]) -> None:
        self._canonical_paths: dict[str, str] = {}
        self._index: dict[str, str] = {}

        for field in fields:
            normalized_canonical = normalize_path(field.canonical_path)
            self._canonical_paths[normalized_canonical] = field.canonical_path
            self._index[normalized_canonical] = field.canonical_path
            self._index[normalize_key(field.canonical_path)] = field.canonical_path
            for alias in field.aliases:
                self._index[normalize_path(alias)] = field.canonical_path
                self._index[normalize_key(alias)] = field.canonical_path

    def resolve(self, raw_key: str) -> str | None:
        normalized_path = normalize_path(raw_key)
        if normalized_path in self._index:
            return self._index[normalized_path]
        normalized_key = normalize_key(raw_key)
        return self._index.get(normalized_key)

    def is_canonical_path(self, raw_path: str) -> bool:
        return normalize_path(raw_path) in self._canonical_paths

    def canonical_path(self, raw_path: str) -> str | None:
        return self._canonical_paths.get(normalize_path(raw_path))
