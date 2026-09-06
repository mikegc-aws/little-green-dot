"""Tests for the credential check. No AWS account or credentials required —
the AWS CLI is replaced with a stub that returns canned responses.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from little_green_dot import aws
from little_green_dot.aws import State, format_remaining, role_name_from_arn
from little_green_dot.config import Config

VALID_IDENTITY = {
    "UserId": "AROAEXAMPLE:mikegc",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/Admin/mikegc",
}


def completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["aws"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


@pytest.fixture
def fake_cli(monkeypatch):
    """Stub out CLI discovery and invocation.

    Register responses keyed by the first CLI argument ('sts' or 'configure').
    """
    responses: dict[str, object] = {}
    calls: list[list[str]] = []

    monkeypatch.setattr(aws, "find_aws_cli", lambda config: "/usr/bin/false")

    def fake_run(cli, args, config):
        calls.append(args)
        response = responses.get(args[0], completed(255, stderr="unstubbed"))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(aws, "_run", fake_run)
    responses["calls"] = calls
    return responses


def test_valid_credentials_are_green(fake_cli):
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))
    fake_cli["configure"] = completed(1)  # no expiry available

    check = aws.check_credentials(Config())

    assert check.state is State.VALID
    assert check.state.dot == "🟢"
    assert check.identity is not None
    assert check.identity.name == "Admin"
    assert check.identity.session == "mikegc"
    assert check.identity.account == "123456789012"
    assert check.expires_at is None


def test_expired_session_is_red(fake_cli):
    fake_cli["sts"] = completed(
        1, stderr="An error occurred (ExpiredToken) when calling the "
                  "GetCallerIdentity operation: The security token included "
                  "in the request is expired",
    )

    check = aws.check_credentials(Config())

    assert check.state is State.INVALID
    assert check.state.dot == "🔴"
    assert check.identity is None
    assert "ExpiredToken" in (check.error or "")


def test_network_failure_is_unknown_not_red(fake_cli):
    """A flaky connection must not claim the credentials are gone."""
    fake_cli["sts"] = completed(
        255, stderr="Could not connect to the endpoint URL: "
                    '"https://sts.amazonaws.com/"',
    )

    check = aws.check_credentials(Config())

    assert check.state is State.UNKNOWN
    assert check.state.dot == "⚪"


def test_timeout_is_unknown(fake_cli):
    fake_cli["sts"] = subprocess.TimeoutExpired(cmd="aws", timeout=10)

    check = aws.check_credentials(Config())

    assert check.state is State.UNKNOWN
    assert "timed out" in (check.error or "")


def test_missing_cli_is_unknown(monkeypatch):
    monkeypatch.setattr(aws, "find_aws_cli", lambda config: None)

    check = aws.check_credentials(Config())

    assert check.state is State.UNKNOWN
    assert check.error == "AWS CLI not found"


def test_expiry_inside_warning_window_is_amber(fake_cli):
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))
    fake_cli["configure"] = completed(0, stdout=json.dumps(
        {"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "s",
         "SessionToken": "t", "Expiration": soon.isoformat()}))

    check = aws.check_credentials(Config(warn_minutes=15))

    assert check.state is State.EXPIRING
    assert check.state.dot == "🟡"
    assert check.remaining_seconds is not None
    assert 0 < check.remaining_seconds <= 15 * 60


def test_already_expired_credentials_are_red(fake_cli):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))
    fake_cli["configure"] = completed(0, stdout=json.dumps(
        {"Version": 1, "Expiration": past.isoformat()}))

    check = aws.check_credentials(Config())

    assert check.state is State.INVALID


def test_show_expiry_false_skips_the_credentials_export(fake_cli):
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))

    check = aws.check_credentials(Config(show_expiry=False))

    assert check.state is State.VALID
    called = [args[0] for args in fake_cli["calls"]]
    assert "configure" not in called, "must not touch credential material"


def test_garbled_response_is_unknown(fake_cli):
    fake_cli["sts"] = completed(0, stdout="not json at all")

    assert aws.check_credentials(Config()).state is State.UNKNOWN


def test_summary_never_includes_secrets(fake_cli):
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))
    fake_cli["configure"] = completed(0, stdout=json.dumps(
        {"Version": 1, "AccessKeyId": "AKIAVERYSECRET",
         "SecretAccessKey": "topsecret", "SessionToken": "tok",
         "Expiration": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}))

    summary = aws.check_credentials(Config()).summary

    assert "AKIAVERYSECRET" not in summary
    assert "topsecret" not in summary
    assert "Admin" in summary


def test_environment_is_scoped_to_the_configured_profile(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    env = aws.build_env(Config(profile="demo", region="eu-west-1"))

    assert env["AWS_PROFILE"] == "demo"
    assert env["AWS_REGION"] == "eu-west-1"
    assert env["AWS_DEFAULT_REGION"] == "eu-west-1"
    assert env["AWS_CLI_AUTO_PROMPT"] == "off"
    assert env["AWS_PAGER"] == ""


def test_pinned_profile_wins_over_ambient_env_credentials(monkeypatch):
    """The AWS CLI ranks env credentials above AWS_PROFILE.

    Left in place, long-lived keys in the environment would be reported as
    healthy while the profile you actually pinned had expired — a false green.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "ambient-token")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "somewhere-else")

    env = aws.build_env(Config(profile="demo"))

    assert env["AWS_PROFILE"] == "demo"
    for leaked in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                   "AWS_SESSION_TOKEN", "AWS_DEFAULT_PROFILE"):
        assert leaked not in env


def test_ambient_credentials_are_left_alone_when_no_profile_is_pinned(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENT")

    env = aws.build_env(Config())

    assert env["AWS_ACCESS_KEY_ID"] == "AKIAAMBIENT"


@pytest.mark.parametrize(
    ("pinned", "environment", "expected"),
    [
        ("demo", {}, "profile demo"),
        ("demo", {"AWS_ACCESS_KEY_ID": "AKIA"}, "profile demo"),
        (None, {"AWS_ACCESS_KEY_ID": "AKIA"}, "environment credentials"),
        (None, {"AWS_PROFILE": "shell"}, "profile shell"),
        (None, {}, "default profile"),
    ],
)
def test_credential_source_is_honest(monkeypatch, pinned, environment, expected):
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID",
                 "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert aws.credential_source(Config(profile=pinned)) == expected


def test_marks_style_needs_no_colour_to_read():
    dots = {aws.glyph(state, "dots") for state in State}
    marks = {aws.glyph(state, "marks") for state in State}

    assert len(dots) == len(marks) == 4
    assert not (dots & marks)
    assert aws.glyph(State.VALID, "marks") == "✓"
    assert aws.glyph(State.INVALID, "marks") == "✕"


def test_unknown_symbol_style_falls_back_to_dots():
    assert aws.glyph(State.VALID, "hieroglyphs") == "🟢"


def test_summary_uses_the_configured_symbols(fake_cli):
    fake_cli["sts"] = completed(0, stdout=json.dumps(VALID_IDENTITY))
    fake_cli["configure"] = completed(1)

    summary = aws.check_credentials(Config(symbols="marks", show_expiry=False)).summary

    assert summary.startswith("✓ Admin")
    assert "🟢" not in summary


@pytest.mark.parametrize(
    ("arn", "expected"),
    [
        ("arn:aws:sts::1:assumed-role/Admin/session", "Admin"),
        ("arn:aws:iam::1:user/Bob", "Bob"),
        ("arn:aws:iam::1:user/team/Bob", "Bob"),
        ("arn:aws:iam::1:root", "root"),
        ("arn:aws:sts::1:federated-user/Carol", "Carol"),
        ("", "unknown"),
    ],
)
def test_role_name_from_arn(arn, expected):
    assert role_name_from_arn(arn) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "unknown"), (0, "expired"), (-5, "expired"), (45, "45s"),
     (600, "10m"), (7500, "2h 05m")],
)
def test_format_remaining(seconds, expected):
    assert format_remaining(seconds) == expected


@pytest.mark.parametrize(
    "value", ["2026-09-07T10:00:00Z", "2026-09-07T10:00:00+00:00", "2026-09-07T12:00:00+02:00"],
)
def test_parse_expiration_normalises_to_utc(value):
    parsed = aws.parse_expiration(value)
    assert parsed == datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)


def test_parse_expiration_rejects_rubbish():
    assert aws.parse_expiration("tomorrow-ish") is None
    assert aws.parse_expiration(None) is None


def test_failure_summary_names_what_it_checked(fake_cli, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    fake_cli["sts"] = completed(1, stderr="ExpiredToken: the token is expired")

    summary = aws.check_credentials(Config(profile="demo")).summary

    assert "profile demo" in summary
    assert "ExpiredToken" in summary
