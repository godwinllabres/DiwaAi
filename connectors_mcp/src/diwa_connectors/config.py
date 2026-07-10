"""Environment-driven configuration.

A tool group is ACTIVE only when its base URL is set and its enabled flag is
not switched off. This is the per-module on/off switch: unset the URL (or set
CONNECTORS_<GROUP>_ENABLED=0) and the group's tools vanish from the menu
without touching any other group.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_TIMEOUT_SECONDS = 8.0  # matches Diwa's per-tool-call ceiling (AIS_MCP_TIMEOUT_SECONDS)

_FALSY = {"0", "false", "no", "off", ""}


def _flag(env: Mapping[str, str], name: str, default: bool = True) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSY


def _base_url(env: Mapping[str, str], name: str) -> str | None:
    raw = (env.get(name) or "").strip().rstrip("/")
    return raw or None


@dataclass(frozen=True)
class GroupConfig:
    base_url: str | None
    enabled: bool

    @property
    def active(self) -> bool:
        return bool(self.base_url) and self.enabled


@dataclass(frozen=True)
class Config:
    courses: GroupConfig
    orps: GroupConfig
    dts: GroupConfig
    timeout_seconds: float


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    try:
        timeout = float(env.get("CONNECTORS_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return Config(
        courses=GroupConfig(
            base_url=_base_url(env, "COURSES_BASE_URL"),
            enabled=_flag(env, "CONNECTORS_COURSES_ENABLED"),
        ),
        orps=GroupConfig(
            base_url=_base_url(env, "ORPS_BASE_URL"),
            enabled=_flag(env, "CONNECTORS_ORPS_ENABLED"),
        ),
        dts=GroupConfig(
            base_url=_base_url(env, "DTS_BASE_URL"),
            enabled=_flag(env, "CONNECTORS_DTS_ENABLED"),
        ),
        timeout_seconds=timeout,
    )
