"""Abstract base for language analyzers (Python, Java)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SymbolTable:
    """Repository-wide symbol table populated during Pass 1."""

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # file_path → list of node IDs declared in that file
    file_nodes: dict[str, list[str]] = field(default_factory=dict)
    # file_path → {alias: qualified_name} — built during pass 1
    file_imports: dict[str, dict[str, str]] = field(default_factory=dict)
    # class_id → {method_name: method_node_id} — built during pass 1
    class_members: dict[str, dict[str, str]] = field(default_factory=dict)
    # simple_name → [node_id, ...] — built during pass 1
    name_index: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class RelationshipSet:
    """Call-graph edges collected during Pass 2 (deduped by semantic id)."""

    _edges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, edge: dict[str, Any]) -> None:
        eid = edge["id"]
        if eid in self._edges:
            # Increment call-site count
            self._edges[eid]["attributes"]["callSiteCount"] = (
                self._edges[eid]["attributes"].get("callSiteCount", 1) + 1
            )
        else:
            self._edges[eid] = edge

    @property
    def edges(self) -> list[dict[str, Any]]:
        return list(self._edges.values())


class LanguageAnalyzer(ABC):
    """Two-pass static analyzer interface."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Language identifier, e.g. 'python' or 'java'."""

    @abstractmethod
    def pass1_declarations(
        self,
        file_path: str,
        source: bytes,
        symbol_table: SymbolTable,
    ) -> None:
        """Extract declarations and populate symbol_table."""

    @abstractmethod
    def pass2_relationships(
        self,
        file_path: str,
        source: bytes,
        symbol_table: SymbolTable,
        relationships: RelationshipSet,
    ) -> None:
        """Resolve relationships using the completed symbol_table."""
