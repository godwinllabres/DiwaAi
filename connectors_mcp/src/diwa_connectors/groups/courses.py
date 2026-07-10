"""Courses-catalog tool group (read-only).

Wraps the `courses` Laravel app's JSON API (routes/api.php). Read endpoints
only — the upstream's write endpoints are deliberately not exposed here.

Route map (verified against the repo, 2026-07-10):
  GET /api/campuses
  GET /api/programs                       GET /api/programs/{id}
  GET /api/programs/{id}/curricula
  GET /api/curricula/{id}/curriculum-subjects
  GET /api/subjects                       GET /api/subjects/{id}
  GET /api/curriculum-subjects/{id}/prerequisite-subjects
"""
from __future__ import annotations

from typing import Any

from ..config import Config
from ..http import get_json, unwrap_list
from ..registry import ToolDef, ToolGroup

_MAX_MATCHES = 25


def _matches(item: Any, query: str) -> bool:
    if not isinstance(item, dict):
        return False
    q = query.lower()
    return any(isinstance(v, str) and q in v.lower() for v in item.values())


def _filtered(payload: dict[str, Any], search: str | None) -> dict[str, Any]:
    if not payload.get("ok"):
        return payload
    items = unwrap_list(payload["data"])
    if search:
        items = [it for it in items if _matches(it, search)]
    total = len(items)
    return {"ok": True, "data": {"total": total, "truncated": total > _MAX_MATCHES, "items": items[:_MAX_MATCHES]}}


def build_group(cfg: Config) -> ToolGroup:
    base = cfg.courses.base_url
    timeout = cfg.timeout_seconds

    async def list_campuses() -> dict[str, Any]:
        return _filtered(await get_json(f"{base}/api/campuses", timeout=timeout), None)

    async def list_programs(search: str | None = None) -> dict[str, Any]:
        return _filtered(await get_json(f"{base}/api/programs", timeout=timeout), search)

    async def get_program(program_id: int) -> dict[str, Any]:
        return await get_json(f"{base}/api/programs/{program_id}", timeout=timeout)

    async def list_program_curricula(program_id: int) -> dict[str, Any]:
        return _filtered(await get_json(f"{base}/api/programs/{program_id}/curricula", timeout=timeout), None)

    async def list_curriculum_subjects(curriculum_id: int) -> dict[str, Any]:
        return _filtered(await get_json(f"{base}/api/curricula/{curriculum_id}/curriculum-subjects", timeout=timeout), None)

    async def find_subject(search: str) -> dict[str, Any]:
        return _filtered(await get_json(f"{base}/api/subjects", timeout=timeout), search)

    async def get_subject_prerequisites(curriculum_subject_id: int) -> dict[str, Any]:
        return await get_json(
            f"{base}/api/curriculum-subjects/{curriculum_subject_id}/prerequisite-subjects",
            timeout=timeout,
        )

    def _int_arg(name: str, description: str) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {name: {"type": "integer", "description": description}},
            "required": [name],
        }

    tools = (
        ToolDef(
            name="courses_list_campuses",
            description="List CvSU campuses in the course catalog.",
            input_schema={"type": "object", "properties": {}},
            handler=list_campuses,
        ),
        ToolDef(
            name="courses_list_programs",
            description="List degree programs, optionally filtered by a search string (name/code).",
            input_schema={
                "type": "object",
                "properties": {"search": {"type": "string", "description": "Substring to match against program fields"}},
            },
            handler=list_programs,
        ),
        ToolDef(
            name="courses_get_program",
            description="Get one degree program by its id.",
            input_schema=_int_arg("program_id", "Program id from courses_list_programs"),
            handler=get_program,
        ),
        ToolDef(
            name="courses_list_program_curricula",
            description="List the curricula offered for a degree program.",
            input_schema=_int_arg("program_id", "Program id from courses_list_programs"),
            handler=list_program_curricula,
        ),
        ToolDef(
            name="courses_list_curriculum_subjects",
            description="List the subjects in a curriculum (per year/semester entries).",
            input_schema=_int_arg("curriculum_id", "Curriculum id from courses_list_program_curricula"),
            handler=list_curriculum_subjects,
        ),
        ToolDef(
            name="courses_find_subject",
            description="Find subjects by code or title substring.",
            input_schema={
                "type": "object",
                "properties": {"search": {"type": "string", "description": "Subject code or title substring"}},
                "required": ["search"],
            },
            handler=find_subject,
        ),
        ToolDef(
            name="courses_get_subject_prerequisites",
            description="List the prerequisite subjects of a curriculum subject.",
            input_schema=_int_arg("curriculum_subject_id", "Curriculum-subject id from courses_list_curriculum_subjects"),
            handler=get_subject_prerequisites,
        ),
    )
    return ToolGroup(name="courses", tools=tools)
