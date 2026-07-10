from diwa_connectors.config import load_config
from diwa_connectors.registry import collect_groups


def test_only_active_groups_collected():
    cfg = load_config(env={"ORPS_BASE_URL": "http://orps.test"})
    groups = collect_groups(cfg)
    assert [g.name for g in groups] == ["orps"]


def test_no_groups_when_nothing_configured():
    assert collect_groups(load_config(env={})) == []


def test_tool_names_are_namespaced_by_group():
    cfg = load_config(
        env={"COURSES_BASE_URL": "http://courses.test", "ORPS_BASE_URL": "http://orps.test"}
    )
    for group in collect_groups(cfg):
        for tool in group.tools:
            assert tool.name.startswith(f"{group.name}_")


def test_no_tool_name_collisions_across_groups():
    cfg = load_config(
        env={"COURSES_BASE_URL": "http://courses.test", "ORPS_BASE_URL": "http://orps.test"}
    )
    names = [t.name for g in collect_groups(cfg) for t in g.tools]
    assert len(names) == len(set(names))
