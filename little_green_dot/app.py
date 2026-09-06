"""The menu bar app.

Design notes:
  * The AWS CLI call happens on a worker thread, so a slow or hanging check can
    never freeze the menu bar.
  * The worker only ever puts a result on a queue. All UI mutation happens on the
    main thread, drained by a short rumps timer.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone

import rumps

from . import aws
from .aws import Check, State
from .config import Config, config_path, load_config

DRAIN_INTERVAL = 0.5  # seconds; how often the main thread applies worker results
STALE_GRACE = 30  # seconds of slack before a missed poll counts as stale


def sentence_case(text: str) -> str:
    """Capitalise the first letter and leave the rest alone.

    Not str.capitalize(), which lowercases the remainder and would misreport a
    profile named "Prod-Admin" as "Prod-admin".
    """
    return text[:1].upper() + text[1:]


def staleness_limit(config: Config, healthy: bool) -> float:
    """How old a result may get before we stop trusting it.

    One poll interval plus room for a slow check, so an ordinary late poll does
    not trip it but a stopped poller does.
    """
    return config.poll_interval(healthy) + max(2 * config.timeout_seconds, STALE_GRACE)


def log(message: str) -> None:
    """Timestamped line to stderr — launchd captures this to the log file."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}",
          file=sys.stderr, flush=True)


class Poller(threading.Thread):
    """Runs credential checks on a schedule and drops results on a queue.

    Careful with attribute names here: threading.Thread uses several private ones
    internally. `_stop` in particular is a Thread *method*, and shadowing it with
    an Event breaks is_alive() as soon as the thread finishes.
    """

    def __init__(self, config: Config, results: queue.Queue[Check]) -> None:
        super().__init__(name="lgd-poller", daemon=True)
        self._config = config
        self._results = results
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._healthy = False

    def check_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                result = aws.check_credentials(self._config)
            except Exception as exc:  # never let the poller die
                log(f"check failed unexpectedly: {exc!r}")
                result = Check(State.UNKNOWN, datetime.now().astimezone(),
                               error="internal error, see log")
            self._healthy = result.state.healthy
            self._results.put(result)

            self._wake.wait(self._config.poll_interval(self._healthy))
            self._wake.clear()


class LittleGreenDot(rumps.App):
    """Green dot = your AWS credentials are live. Red dot = they are not."""

    def __init__(self, config: Config, warnings: list[str] | None = None) -> None:
        super().__init__("little-green-dot", title="⚪", quit_button="Quit")
        self._config = config
        self._warnings = warnings or []
        self._results: queue.Queue[Check] = queue.Queue()
        self._last: Check | None = None
        self._last_state: State | None = None
        self._stale = False

        # Initial titles double as the keys rumps uses for its menu, so they need
        # to be distinct even though the first poll overwrites them.
        self.identity_item = rumps.MenuItem("AWS: checking…")
        self.arn_item = rumps.MenuItem("ARN", callback=self.copy_arn)
        self.account_item = rumps.MenuItem("Account")
        self.expiry_item = rumps.MenuItem("Expires")
        self.checked_item = rumps.MenuItem("Last checked: —")
        self.config_item = rumps.MenuItem("Edit Config…", callback=self.open_config)

        self.menu = [
            self.identity_item,
            self.arn_item,
            self.account_item,
            self.expiry_item,
            rumps.separator,
            self.checked_item,
            rumps.MenuItem("Check Now", callback=self.check_now),
            rumps.separator,
            self.config_item,
        ]
        for item in (self.arn_item, self.account_item, self.expiry_item):
            item.hidden = True

        if self._warnings:
            for warning in self._warnings:
                log(f"config: {warning}")

        self._poller = Poller(config, self._results)
        self._timer = rumps.Timer(self._drain, DRAIN_INTERVAL)

    # --- lifecycle ---------------------------------------------------------

    def run(self, **options) -> None:  # type: ignore[override]
        self._timer.start()
        self._poller.start()
        try:
            super().run(**options)
        finally:
            self._poller.stop()

    # --- menu callbacks ----------------------------------------------------

    def check_now(self, _sender=None) -> None:
        self.checked_item.title = "Last checked: checking now…"
        self._poller.check_now()

    def copy_arn(self, _sender=None) -> None:
        if self._last is None or self._last.identity is None:
            return
        try:
            subprocess.run(["/usr/bin/pbcopy"], input=self._last.identity.arn,
                           text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"could not copy ARN: {exc}")

    def open_config(self, _sender=None) -> None:
        path = config_path()
        try:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_STARTER_CONFIG, encoding="utf-8")
            subprocess.run(["/usr/bin/open", "-t", str(path)], timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"could not open config: {exc}")

    # --- UI updates (main thread only) -------------------------------------

    def _drain(self, _timer=None) -> None:
        latest: Check | None = None
        while True:
            try:
                latest = self._results.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._apply(latest)
        self._check_freshness()

    def _check_freshness(self) -> None:
        """Never let a stale answer keep claiming everything is fine.

        A green dot that stopped updating is worse than no dot at all: it invites
        exactly the false confidence this tool exists to prevent. If the last
        result has aged out — poller thread wedged inside a hung AWS CLI, machine
        back from a long sleep, thread died outright — the display drops to
        "unknown" and says how old the answer is.
        """
        if self._last is None:
            return
        age = (datetime.now(timezone.utc) - self._last.checked_at).total_seconds()
        if age <= staleness_limit(self._config, self._last.state.healthy):
            return

        if not self._stale:
            self._stale = True
            log(f"last check is {int(age)}s old; showing unknown until it refreshes")
            self._poller.check_now()

        self.title = self._title_for(None)
        self.identity_item.title = (
            f"AWS: ? no answer for {aws.format_remaining(age)} — checking"
        )
        self._set_tooltip(
            f"{aws.glyph(State.UNKNOWN, self._config.symbols)} AWS: last answer was "
            f"{aws.format_remaining(age)} ago"
        )
        for item in (self.arn_item, self.account_item, self.expiry_item):
            item.hidden = True

        # A dead thread cannot recover on its own; a merely wedged one is left
        # alone rather than leaking a replacement every half second.
        if not self._poller.is_alive():
            log("poller thread is dead; starting a replacement")
            self._poller = Poller(self._config, self._results)
            self._poller.start()

    def _apply(self, check: Check) -> None:
        self._last = check
        self._stale = False
        self.title = self._title_for(check)
        self._set_tooltip(check.describe(include_account=self._config.show_account))

        if check.identity is not None:
            name = check.identity.name
            # The session name is usually a username, so it belongs with the
            # other identifying detail that show_arn controls.
            session = check.identity.session if self._config.show_arn else None
            self.identity_item.title = f"AWS: ✓ {name}" + (f"  ({session})" if session else "")
            self.arn_item.title = check.identity.arn
            self.arn_item.hidden = not self._config.show_arn
            self.account_item.title = f"Account: {check.identity.account}"
            self.account_item.hidden = not self._config.show_account
        else:
            reason = check.error or check.state.value
            mark = "✕" if check.state is State.INVALID else "?"
            self.identity_item.title = f"AWS: {mark} {reason}"
            self.arn_item.hidden = True
            self.account_item.hidden = True

        if check.expires_at is not None:
            remaining = aws.format_remaining(check.remaining_seconds)
            local = check.expires_at.astimezone()
            self.expiry_item.title = f"Expires: {remaining}  ({local.strftime('%H:%M')})"
            self.expiry_item.hidden = False
        else:
            self.expiry_item.hidden = True

        stamp = check.checked_at.astimezone().strftime("%H:%M:%S")
        self.checked_item.title = f"{sentence_case(check.source)} · checked {stamp}"

        self._maybe_notify(check)
        self._last_state = check.state

    def _title_for(self, check: Check | None) -> str:
        """Menu bar text. `None` means we no longer trust what we last knew."""
        if check is None:
            return aws.glyph(State.UNKNOWN, self._config.symbols)
        if not self._config.show_label or check.identity is None:
            return check.glyph
        return f"{check.glyph} {check.identity.name}"

    def _set_tooltip(self, text: str) -> None:
        """Hover text on the menu bar item. Best effort — never fatal."""
        try:
            nsapp = getattr(self, "_nsapp", None)  # only exists once run() starts
            status_item = getattr(nsapp, "nsstatusitem", None)
            button = status_item.button() if status_item is not None else None
            if button is not None:
                button.setToolTip_(text)
        except Exception:  # pragma: no cover - AppKit edge cases
            pass

    def _maybe_notify(self, check: Check) -> None:
        if not self._config.notify or self._last_state is None:
            return
        if check.state is self._last_state:
            return
        title, message = _notification_for(check)
        if title is None:
            return
        try:
            rumps.notification(title, "little-green-dot", message)
        except Exception as exc:  # pragma: no cover - unbundled apps can't notify
            log(f"notification suppressed: {exc}")


def _notification_for(check: Check) -> tuple[str | None, str]:
    if check.state is State.INVALID:
        return "AWS credentials are not active", check.error or "Log in again to go green."
    if check.state is State.EXPIRING:
        return ("AWS credentials expiring soon",
                f"About {aws.format_remaining(check.remaining_seconds)} left.")
    if check.state is State.VALID and check.identity is not None:
        return "AWS credentials active", f"Signed in as {check.identity.name}."
    return None, ""


_STARTER_CONFIG = """# little-green-dot configuration. Every option is optional.

# Which credentials to watch. Leave unset to use whatever AWS_PROFILE says.
# profile = "my-profile"
# region = "us-east-1"

# interval_seconds = 60          # how often to check while things look good
# invalid_interval_seconds = 15  # how often to re-check once the dot is red
# timeout_seconds = 10

# symbols = "dots"    # "dots" for 🟢🔴, or "marks" for ✓✕ (no colour needed)
# show_label = false   # show the role name next to the dot in the menu bar
# show_account = true  # show the account id in the dropdown
# show_arn = true      # show the full ARN row (careful when screen sharing)
# show_expiry = true   # look up credential expiry (see README security note)
# warn_minutes = 15    # amber dot once expiry is this close
# notify = true        # macOS notification when the state changes
"""
