"""Configuration loading for little-green-dot.

Config lives in a small TOML file. Every field is optional — with no config file
at all the tool behaves like the original clu indicator did: it checks whatever
AWS credentials the environment already points at.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

APP_NAME = "little-green-dot"
CONFIG_ENV = "LITTLE_GREEN_DOT_CONFIG"

# Profile / region / path values are handed to the AWS CLI through the
# environment. We never build a shell string, but we still keep these to a
# conservative character set so a hand-edited config cannot smuggle anything odd
# into a child process.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._@:+=,-]+$")


class ConfigError(Exception):
    """Raised when the config file exists but cannot be used as written."""


@dataclass(frozen=True)
class Config:
    """Everything the indicator needs to know, with sane defaults."""

    # Which credentials to watch. None means "whatever the environment says".
    profile: str | None = None
    region: str | None = None

    # Polling. A shorter interval is used while credentials look bad so the dot
    # turns green promptly after you log in somewhere else.
    interval_seconds: int = 60
    invalid_interval_seconds: int = 15
    timeout_seconds: int = 10

    # Menu bar appearance.
    show_label: bool = False  # put the role name next to the dot
    show_account: bool = True  # show the account id in the dropdown
    show_expiry: bool = True  # look up and display credential expiry
    warn_minutes: int = 15  # amber dot once expiry is this close

    notify: bool = True  # macOS notification on state change
    aws_cli_path: str | None = None  # override AWS CLI discovery

    def poll_interval(self, healthy: bool) -> int:
        return self.interval_seconds if healthy else self.invalid_interval_seconds


def config_path() -> Path:
    """Location of the config file, honouring $LITTLE_GREEN_DOT_CONFIG."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / APP_NAME / "config.toml"


def load_config(path: Path | None = None) -> tuple[Config, list[str]]:
    """Load config from disk.

    Returns the config plus a list of human-readable problems. A broken config
    never stops the indicator from running — we fall back to defaults for the
    fields we could not read and surface the problem in the menu instead.
    """
    path = path or config_path()
    if not path.exists():
        return Config(), []

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Config(), [f"Could not read {path.name}: {exc}"]

    return _from_mapping(raw, source=path.name)


def _from_mapping(raw: dict, source: str = "config") -> tuple[Config, list[str]]:
    config = Config()
    problems: list[str] = []
    known = {f: getattr(config, f) for f in Config.__dataclass_fields__}

    for key, value in raw.items():
        if key not in known:
            problems.append(f"{source}: unknown option '{key}' ignored")
            continue
        try:
            config = replace(config, **{key: _coerce(key, value, known[key])})
        except ConfigError as exc:
            problems.append(f"{source}: {exc}")

    if config.interval_seconds < 5 or config.invalid_interval_seconds < 5:
        problems.append(f"{source}: poll intervals below 5s ignored")
        config = replace(
            config,
            interval_seconds=max(config.interval_seconds, 5),
            invalid_interval_seconds=max(config.invalid_interval_seconds, 5),
        )
    if config.timeout_seconds < 1:
        problems.append(f"{source}: timeout_seconds below 1s ignored")
        config = replace(config, timeout_seconds=1)

    return config, problems


def _coerce(key: str, value: object, default: object):
    """Validate one config value against the type implied by its default."""
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"'{key}' must be true or false")
        return value

    if isinstance(default, int) and not isinstance(default, bool):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"'{key}' must be a whole number")
        return value

    # Remaining fields are optional strings.
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"'{key}' must be a string")
    if key == "aws_cli_path":
        return value
    if not _SAFE_NAME.match(value):
        raise ConfigError(f"'{key}' contains unsupported characters")
    return value
