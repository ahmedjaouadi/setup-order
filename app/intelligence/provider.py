from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    async def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class DisabledLLMProvider(LLMProvider):
    async def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "disabled",
            "enabled": False,
            "draft": None,
            "message": "LLM provider is disabled in this phase.",
        }
