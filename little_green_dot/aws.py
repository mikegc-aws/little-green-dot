"""AWS credential checks.

The only thing that decides the colour of the dot is `aws sts get-caller-identity`.
Everything here shells out to the AWS CLI with an argument list (never a shell
string), always with a timeout, and never writes credential material anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .config import Config

# Fallbacks for when we are started by launchd, which gives us a minimal PATH.
_CLI_FALLBACKS = (
    "/opt/homebrew/bin/aws",
    "/usr/local/bin/aws",
    "/usr/bin/aws",
)


class State(str, Enum):
    """What the dot is telling you."""

    VALID = "valid"  # credentials work
    EXPIRING = "expiring"  # they work, but not for much longer
    INVALID = "invalid"  # AWS told us no
    UNKNOWN = "unknown"  # we could not find out (no CLI, timeout, offline)

    @property
    def dot(self) -> str:
        return {
            State.VALID: "🟢",
            State.EXPIRING: "🟡",
            State.INVALID: "🔴",
            State.UNKNOWN: "⚪",
        }[self]

    @property
    def healthy(self) -> bool:
        return self in (State.VALID, State.EXPIRING)


@dataclass(frozen=True)
class Identity:
    """The interesting parts of a get-caller-identity response."""

    arn: str
    account: str
    user_id: str

    @property
    def name(self) -> str:
        """Friendly name for the role or user behind these credentials."""
        return role_name_from_arn(self.arn)

    @property
    def session(self) -> str | None:
        """Session name, for assumed-role ARNs."""
        parts = _arn_resource(self.arn).split("/")
        if parts[0] == "assumed-role" and len(parts) >= 3:
            return parts[2]
        return None


@dataclass(frozen=True)
class Check:
    """Result of one poll."""

    state: State
    checked_at: datetime
    identity: Identity | None = None
    expires_at: datetime | None = None
    profile: str | None = None
    region: str | None = None
    error: str | None = None

    @property
    def summary(self) -> str:
        """One line suitable for a tooltip or a terminal."""
        if self.identity is None:
            return f"{self.state.dot} AWS: {self.error or self.state.value}"
        bits = [f"{self.state.dot} {self.identity.name}"]
        if self.profile:
            bits.append(f"profile {self.profile}")
        bits.append(f"account {self.identity.account}")
        if self.expires_at:
            bits.append(f"expires in {format_remaining(self.remaining_seconds)}")
        return " · ".join(bits)

    @property
    def remaining_seconds(self) -> float | None:
        if self.expires_at is None:
            return None
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()


def _arn_resource(arn: str) -> str:
    """The resource portion of an ARN, e.g. 'assumed-role/Admin/mikegc'."""
    parts = arn.split(":", 5)
    return parts[5] if len(parts) == 6 else arn


def role_name_from_arn(arn: str) -> str:
    """Pull the role or user name out of an STS/IAM ARN."""
    if not arn:
        return "unknown"
    resource = _arn_resource(arn)
    parts = [p for p in resource.split("/") if p]
    if not parts:
        return arn
    if parts[0] == "assumed-role" and len(parts) >= 2:
        return parts[1]
    if len(parts) == 1:
        return parts[0]  # e.g. 'root'
    return parts[-1]  # e.g. user/Bob, federated-user/Bob


def format_remaining(seconds: float | None) -> str:
    """'2h 05m', '14m', 'expired'."""
    if seconds is None:
        return "unknown"
    if seconds <= 0:
        return "expired"
    minutes, hours = int(seconds // 60) % 60, int(seconds // 3600)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{int(seconds)}s"


def parse_expiration(value: str | None) -> datetime | None:
    """Parse an AWS expiry timestamp into an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def find_aws_cli(config: Config) -> str | None:
    """Locate the AWS CLI, tolerating launchd's stripped-down PATH."""
    if config.aws_cli_path:
        candidate = Path(config.aws_cli_path).expanduser()
        return str(candidate) if candidate.is_file() else None
    found = shutil.which("aws")
    if found:
        return found
    return next((p for p in _CLI_FALLBACKS if Path(p).is_file()), None)


def build_env(config: Config) -> dict[str, str]:
    """Environment for the CLI child process."""
    env = dict(os.environ)
    if config.profile:
        env["AWS_PROFILE"] = config.profile
    if config.region:
        env["AWS_REGION"] = config.region
        env["AWS_DEFAULT_REGION"] = config.region
    # Keep the CLI non-interactive and unpaged so it can never block on us.
    env["AWS_PAGER"] = ""
    env["AWS_CLI_AUTO_PROMPT"] = "off"
    return env


def _run(cli: str, args: list[str], config: Config) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [cli, *args],
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        env=build_env(config),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def check_credentials(config: Config) -> Check:
    """Ask STS who we are. This is the whole point of the tool."""
    now = datetime.now(timezone.utc)
    profile = config.profile or os.environ.get("AWS_PROFILE")
    region = config.region or os.environ.get("AWS_REGION")

    cli = find_aws_cli(config)
    if cli is None:
        return Check(State.UNKNOWN, now, profile=profile, region=region,
                     error="AWS CLI not found")

    try:
        result = _run(cli, ["sts", "get-caller-identity", "--output", "json"], config)
    except subprocess.TimeoutExpired:
        return Check(State.UNKNOWN, now, profile=profile, region=region,
                     error=f"timed out after {config.timeout_seconds}s")
    except OSError as exc:
        return Check(State.UNKNOWN, now, profile=profile, region=region, error=str(exc))

    if result.returncode != 0:
        return Check(
            classify_cli_error(result.stderr),
            now,
            profile=profile,
            region=region,
            error=first_line(result.stderr) or f"aws exited {result.returncode}",
        )

    try:
        payload = json.loads(result.stdout)
        identity = Identity(
            arn=str(payload["Arn"]),
            account=str(payload.get("Account", "")),
            user_id=str(payload.get("UserId", "")),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return Check(State.UNKNOWN, now, profile=profile, region=region,
                     error="unexpected response from aws sts")

    expires_at = fetch_expiry(cli, config) if config.show_expiry else None
    state = State.VALID
    if expires_at is not None:
        remaining = (expires_at - now).total_seconds()
        if remaining <= 0:
            state = State.INVALID
        elif remaining <= config.warn_minutes * 60:
            state = State.EXPIRING

    return Check(state, now, identity=identity, expires_at=expires_at,
                 profile=profile, region=region)


def classify_cli_error(stderr: str) -> State:
    """Distinguish "AWS said no" from "we could not reach AWS".

    A network blip should not claim your credentials are gone — that is the kind
    of false alarm the dot exists to prevent.
    """
    text = (stderr or "").lower()
    unreachable = (
        "could not connect",
        "endpoint url",
        "connect timeout",
        "read timeout",
        "connection was closed",
        "name or service not known",
        "temporary failure in name resolution",
        "ssl",
        "certificate verify failed",
        "proxy",
    )
    if any(marker in text for marker in unreachable):
        return State.UNKNOWN
    return State.INVALID


def fetch_expiry(cli: str, config: Config) -> datetime | None:
    """Best-effort credential expiry via `aws configure export-credentials`.

    Security note: that command's stdout contains live credential material. We
    parse the Expiration field in memory and never log, print, or persist the
    response — and we swallow the body entirely on any error path.
    """
    try:
        result = _run(cli, ["configure", "export-credentials", "--format", "process"], config)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    finally:
        result = None  # drop our reference to the credential payload
    if not isinstance(payload, dict):
        return None
    expiration = parse_expiration(payload.get("Expiration"))
    payload.clear()
    return expiration


def first_line(text: str | None, limit: int = 160) -> str:
    """First meaningful line of CLI stderr, trimmed for display."""
    if not text:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""
