# little-green-dot

A tiny macOS menu bar indicator that answers one question at a glance:

**Do I have live AWS credentials right now?**

```
🟢  credentials are active
🟡  active, but expiring within 15 minutes
🔴  AWS says no — you are not logged in
⚪  can't tell (offline, AWS CLI missing, timed out)
```

Click the dot and it tells you which role you are, the account, and how long you
have left. Hover and the same summary appears as a tooltip. It re-checks on a
timer, so the dot you glanced at two minutes ago is still true — which is the
whole point when you are about to share your screen.

<!-- Screenshot: drop an image of the menu bar dropdown here. -->

## Why

Nothing is more annoying than starting a live demo, running one command, and
discovering your session expired twenty minutes ago. This started life as one
small piece of a larger personal agent; the dot outlived the agent.

## How it decides

One call, the same one you would run by hand:

```bash
aws sts get-caller-identity
```

Exit 0 means green. A credentials error means red. A *network* error means white,
not red — a flaky wifi connection should never tell you your session is gone.

## Install

Requires macOS, Python 3.11+, and the AWS CLI on your `PATH`.

```bash
git clone https://github.com/mikegc-aws/little-green-dot.git
cd little-green-dot
./install.sh
```

`install.sh` creates a local virtual environment, installs the one dependency
(`rumps`), and registers a launch agent so the dot comes back after a reboot. It
prints everything it is about to do and touches nothing outside this directory,
`~/Library/LaunchAgents/`, and `~/Library/Logs/`.

To remove it completely:

```bash
./uninstall.sh
```

### Run it by hand instead

```bash
uv run little-green-dot          # or: .venv/bin/little-green-dot
```

### Check credentials without the GUI

Useful for a shell prompt, a script, or working out why the dot is red:

```bash
little-green-dot --once
# 🟢 Admin · profile my-profile · account 123456789012 · expires in 2h 41m

little-green-dot --once --json    # machine readable
```

Exit status is `0` when credentials are usable and `1` when they are not, so
`little-green-dot --once >/dev/null && terraform apply` does what you would hope.

## Configuration

Everything is optional. `little-green-dot --where` prints the path
(`~/.config/little-green-dot/config.toml`), and **Edit Config…** in the menu
creates a starter file and opens it.

```toml
profile = "my-profile"           # default: whatever AWS_PROFILE says
region = "us-east-1"

interval_seconds = 60            # check frequency while things look good
invalid_interval_seconds = 15    # faster re-checks while the dot is red,
                                 # so it goes green promptly after you log in
timeout_seconds = 10

show_label = false               # put the role name next to the dot
show_account = true              # show the account id in the dropdown
show_expiry = true               # look up expiry (see Security below)
warn_minutes = 15                # amber dot once expiry is this close
notify = true                    # notification when the state changes

# aws_cli_path = "/opt/homebrew/bin/aws"   # if it isn't on your PATH
```

Restart the app to pick up changes (menu **Quit**, then relaunch, or
`launchctl kickstart -k gui/$UID/com.github.mikegc-aws.little-green-dot`).

### Watching more than one profile

Run more than one copy, each with its own config:

```bash
LITTLE_GREEN_DOT_CONFIG=~/.config/little-green-dot/prod.toml \
  .venv/bin/little-green-dot
```

Set `show_label = true` in each so you can tell the dots apart.

## Security

This is a small tool that watches your credentials, so it is worth being precise
about what it does:

- **It never stores, prints, or transmits credentials.** No files are written
  other than the config file you edit and a plain-text activity log.
- **It only ever runs the AWS CLI**, as a fixed argument list with a timeout and
  no shell involved. There is no way to configure a command for it to execute.
- **It makes no network calls of its own.** The only traffic is the AWS CLI
  talking to the STS endpoint.
- **`show_expiry` needs one extra call:** `aws configure export-credentials`,
  whose output includes live credential material. The response is parsed in
  memory for the `Expiration` field only and is never logged or written to disk.
  If you would rather it never run, set `show_expiry = false`.
- **The log** (`~/Library/Logs/little-green-dot.log`) records config problems and
  errors. Error text comes from the AWS CLI and contains no secrets, but it can
  include your profile name and account id.
- **The launch agent** stores no secrets — just a `PATH` and this directory.

## How it fits together

| File | Role |
| --- | --- |
| `little_green_dot/aws.py` | The credential check. No UI, no dependencies. |
| `little_green_dot/config.py` | Loading and validating the TOML config. |
| `little_green_dot/app.py` | The menu bar app and its polling thread. |
| `little_green_dot/cli.py` | Argument parsing and the `--once` modes. |
| `install.sh` / `uninstall.sh` | Launch agent setup and removal. |

The check runs on a worker thread and hands results to the main thread through a
queue, so a slow or hanging AWS call can't freeze the menu bar.

```bash
uv run pytest        # the test suite mocks the CLI, so it needs no credentials
```

## Troubleshooting

**The dot is white.** Either the AWS CLI isn't on the launch agent's `PATH` (set
`aws_cli_path`) or the check is failing. Run `little-green-dot --once` in a
terminal to see the actual error, and check
`~/Library/Logs/little-green-dot.log`.

**No dot at all.** Check the log, then:
`launchctl print gui/$UID/com.github.mikegc-aws.little-green-dot`.

**No notifications.** macOS only shows notifications from signed app bundles for
some configurations; running from source they are best-effort. The dot itself is
always accurate — set `notify = false` if the failures bother you.

**It says red but I just logged in.** The dot re-checks every
`invalid_interval_seconds` (15 by default). Use **Check Now** to skip the wait.

## License

MIT — see [LICENSE](LICENSE).
