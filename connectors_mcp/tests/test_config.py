from diwa_connectors.config import DEFAULT_TIMEOUT_SECONDS, load_config


def test_group_inactive_without_base_url():
    cfg = load_config(env={})
    assert not cfg.courses.active
    assert not cfg.orps.active


def test_group_active_with_base_url():
    cfg = load_config(env={"COURSES_BASE_URL": "http://courses.test/"})
    assert cfg.courses.active
    assert cfg.courses.base_url == "http://courses.test"  # trailing slash stripped
    assert not cfg.orps.active


def test_enabled_flag_switches_group_off():
    cfg = load_config(
        env={"COURSES_BASE_URL": "http://courses.test", "CONNECTORS_COURSES_ENABLED": "0"}
    )
    assert not cfg.courses.active


def test_timeout_default_and_override():
    assert load_config(env={}).timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert load_config(env={"CONNECTORS_TIMEOUT_SECONDS": "3.5"}).timeout_seconds == 3.5
    assert load_config(env={"CONNECTORS_TIMEOUT_SECONDS": "bogus"}).timeout_seconds == DEFAULT_TIMEOUT_SECONDS
