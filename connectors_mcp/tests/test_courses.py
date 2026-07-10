import httpx
import respx

from diwa_connectors.config import load_config
from diwa_connectors.groups import courses

BASE = "http://courses.test"


def _handlers():
    cfg = load_config(env={"COURSES_BASE_URL": BASE})
    group = courses.build_group(cfg)
    return {t.name: t.handler for t in group.tools}


PROGRAMS = [
    {"id": 1, "name": "BS Computer Science", "code": "BSCS"},
    {"id": 2, "name": "BS Information Technology", "code": "BSIT"},
    {"id": 3, "name": "BS Agriculture", "code": "BSA"},
]


@respx.mock
async def test_list_programs_plain_array():
    respx.get(f"{BASE}/api/programs").mock(return_value=httpx.Response(200, json=PROGRAMS))
    out = await _handlers()["courses_list_programs"]()
    assert out["ok"] and out["data"]["total"] == 3
    assert out["data"]["truncated"] is False


@respx.mock
async def test_list_programs_laravel_wrapper_and_search():
    respx.get(f"{BASE}/api/programs").mock(
        return_value=httpx.Response(200, json={"data": PROGRAMS, "links": {}, "meta": {}})
    )
    out = await _handlers()["courses_list_programs"](search="computer")
    assert out["ok"] and out["data"]["total"] == 1
    assert out["data"]["items"][0]["code"] == "BSCS"


@respx.mock
async def test_find_subject_filters_by_code():
    subjects = [
        {"id": 10, "code": "COSC 101", "title": "Introduction to Computing"},
        {"id": 11, "code": "MATH 101", "title": "Calculus I"},
    ]
    respx.get(f"{BASE}/api/subjects").mock(return_value=httpx.Response(200, json=subjects))
    out = await _handlers()["courses_find_subject"](search="cosc")
    assert out["ok"] and out["data"]["total"] == 1
    assert out["data"]["items"][0]["id"] == 10


@respx.mock
async def test_prerequisites_uses_prerequisite_subjects_route():
    route = respx.get(f"{BASE}/api/curriculum-subjects/77/prerequisite-subjects").mock(
        return_value=httpx.Response(200, json=[{"id": 5, "code": "COSC 100"}])
    )
    out = await _handlers()["courses_get_subject_prerequisites"](curriculum_subject_id=77)
    assert route.called
    assert out["ok"] and out["data"][0]["code"] == "COSC 100"


CURRICULUM_SUBJECTS = [
    {"id": 70, "subject_code": "COSC 101", "subject_title": "Introduction to Computing"},
    {"id": 77, "subject_code": "DCIT 26", "subject_title": "Applications Development"},
]


@respx.mock
async def test_get_prerequisites_two_hop_chain():
    respx.get(f"{BASE}/api/curricula/5/curriculum-subjects").mock(
        return_value=httpx.Response(200, json=CURRICULUM_SUBJECTS)
    )
    prereq_route = respx.get(f"{BASE}/api/curriculum-subjects/77/prerequisite-subjects").mock(
        return_value=httpx.Response(200, json=[{"id": 70, "subject_code": "COSC 101"}])
    )
    out = await _handlers()["courses_get_prerequisites"](curriculum_id=5, subject_code="dcit26")
    assert prereq_route.called
    assert out["ok"] and out["data"]["found"] is True
    assert out["data"]["curriculum_subject"]["id"] == 77
    assert out["data"]["prerequisites"][0]["subject_code"] == "COSC 101"


@respx.mock
async def test_get_prerequisites_subject_not_in_curriculum():
    respx.get(f"{BASE}/api/curricula/5/curriculum-subjects").mock(
        return_value=httpx.Response(200, json=CURRICULUM_SUBJECTS)
    )
    out = await _handlers()["courses_get_prerequisites"](curriculum_id=5, subject_code="MATH 999")
    assert out["ok"] and out["data"]["found"] is False


@respx.mock
async def test_result_cap_marks_truncated():
    many = [{"id": i, "name": f"Program {i}"} for i in range(40)]
    respx.get(f"{BASE}/api/programs").mock(return_value=httpx.Response(200, json=many))
    out = await _handlers()["courses_list_programs"]()
    assert out["data"]["total"] == 40
    assert out["data"]["truncated"] is True
    assert len(out["data"]["items"]) == 25
