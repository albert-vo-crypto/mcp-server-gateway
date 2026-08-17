"""
Adapter protocol — any framework can wire into the optimizer by exposing
``to_items`` + ``from_items`` (round-trip lossless for what the optimizer
needs to see).
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from ..core.item import ContextItem


@runtime_checkable
class Adapter(Protocol):
    """Round-trip converter for one framework's native message format."""

    name: str

    def to_items(self, messages) -> List[ContextItem]: ...

    def from_items(self, items: List[ContextItem]): ...
