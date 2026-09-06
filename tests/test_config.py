"""Tests for config loading. A bad config must degrade, never crash."""

from __future__ import annotations

from little_green_dot.config import CONFIG_ENV, Config, config_path, load_config


def write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(tmp_path):
    config, problems = load_config(tmp_path / "nope.toml")

    assert config == Config()
    assert problems == []


def test_full_config_is_read(tmp_path):
    path = write(tmp_path, """
        profile = "demo"
        region = "eu-west-2"
        interval_seconds = 30
        show_label = true
        show_expiry = false
        warn_minutes = 5
    """)

    config, problems = load_config(path)

    assert problems == []
    assert config.profile == "demo"
    assert config.region == "eu-west-2"
    assert config.interval_seconds == 30
    assert config.show_label is True
    assert config.show_expiry is False
    assert config.warn_minutes == 5
    assert config.notify is True  # untouched default


def test_invalid_toml_falls_back_to_defaults(tmp_path):
    path = write(tmp_path, "this is not = = toml")

    config, problems = load_config(path)

    assert config == Config()
    assert len(problems) == 1
    assert "Could not read" in problems[0]


def test_wrong_types_are_reported_and_ignored(tmp_path):
    path = write(tmp_path, """
        interval_seconds = "sixty"
        show_label = "yes"
        profile = 42
    """)

    config, problems = load_config(path)

    assert config == Config()
    assert len(problems) == 3


def test_unknown_option_is_reported(tmp_path):
    path = write(tmp_path, 'colour = "purple"')

    config, problems = load_config(path)

    assert config == Config()
    assert "unknown option 'colour'" in problems[0]


def test_hostile_profile_name_is_rejected(tmp_path):
    path = write(tmp_path, 'profile = "demo; rm -rf ~"')

    config, problems = load_config(path)

    assert config.profile is None
    assert "unsupported characters" in problems[0]


def test_profile_names_aws_actually_allows(tmp_path):
    path = write(tmp_path, 'profile = "my-org_prod.admin+ci"')

    config, problems = load_config(path)

    assert config.profile == "my-org_prod.admin+ci"
    assert problems == []


def test_absurd_intervals_are_clamped(tmp_path):
    path = write(tmp_path, """
        interval_seconds = 0
        invalid_interval_seconds = 1
        timeout_seconds = 0
    """)

    config, problems = load_config(path)

    assert config.interval_seconds == 5
    assert config.invalid_interval_seconds == 5
    assert config.timeout_seconds == 1
    assert len(problems) == 2


def test_empty_string_means_unset(tmp_path):
    path = write(tmp_path, 'profile = ""')

    config, problems = load_config(path)

    assert config.profile is None
    assert problems == []


def test_poll_interval_speeds_up_when_unhealthy():
    config = Config(interval_seconds=60, invalid_interval_seconds=15)

    assert config.poll_interval(healthy=True) == 60
    assert config.poll_interval(healthy=False) == 15


def test_config_path_honours_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / "custom.toml"))

    assert config_path() == tmp_path / "custom.toml"


def test_config_path_default_location(monkeypatch, tmp_path):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "little-green-dot" / "config.toml"


def test_symbols_accepts_the_known_styles(tmp_path):
    for style in ("dots", "marks"):
        config, problems = load_config(write(tmp_path, f'symbols = "{style}"'))
        assert config.symbols == style
        assert problems == []


def test_unknown_symbols_style_is_rejected(tmp_path):
    config, problems = load_config(write(tmp_path, 'symbols = "sparkles"'))

    assert config.symbols == "dots"
    assert "symbols must be one of" in problems[0]


def test_privacy_switches_can_be_turned_off(tmp_path):
    config, problems = load_config(write(tmp_path, """
        show_arn = false
        show_account = false
    """))

    assert config.show_arn is False
    assert config.show_account is False
    assert problems == []
