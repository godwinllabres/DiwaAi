"""Tool/group registry.

A ToolGroup is one system's vetted menu. Tool names are namespaced by group
(courses_*, orps_*) so menus from several MCP servers can merge inside Diwa
without collisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import Config

Handler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler


@dataclass(frozen=True)
class ToolGroup:
    name: str
    tools: tuple[ToolDef, ...]


def collect_groups(cfg: Config) -> list[ToolGroup]:
    from .groups import courses, orps

    groups: list[ToolGroup] = []
    if cfg.courses.active:
        groups.append(courses.build_group(cfg))
    if cfg.orps.active:
        groups.append(orps.build_group(cfg))
    return groups
