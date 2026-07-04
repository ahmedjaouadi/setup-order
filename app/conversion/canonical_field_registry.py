from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CanonicalField:
    canonical_path: str
    aliases: tuple[str, ...]


def _default_alias_file() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "field_aliases.yaml"


@lru_cache(maxsize=1)
def load_canonical_fields(alias_file: str | None = None) -> tuple[CanonicalField, ...]:
    path = Path(alias_file) if alias_file else _default_alias_file()
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load canonical field aliases.") from exc

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"Field alias registry must be a mapping: {path}")

    fields: list[CanonicalField] = []
    for canonical_path, aliases in payload.items():
        if not isinstance(canonical_path, str):
            raise ValueError(f"Canonical field path must be text: {canonical_path!r}")
        if aliases is None:
            alias_values: tuple[str, ...] = ()
        elif isinstance(aliases, list) and all(isinstance(item, str) for item in aliases):
            alias_values = tuple(aliases)
        else:
            raise ValueError(f"Aliases for {canonical_path} must be a list of strings.")
        fields.append(
            CanonicalField(
                canonical_path=canonical_path,
                aliases=alias_values,
            )
        )
    return tuple(fields)
