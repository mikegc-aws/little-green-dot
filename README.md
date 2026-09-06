# little-green-dot

A tiny macOS menu bar indicator that answers one question at a glance:

**Do I have live AWS credentials right now?**

```
🟢  credentials are active
🟡  active, but expiring within 15 minutes
🔴  AWS says no — you are not logged in
⚪  can't tell (offline, AWS CLI missing, timed out, or the answer went stale)
```

Red and green dots are the same shape, which is no help if you have red-green
colour blindness. Set `symbols = "marks"` for `✓ ! ✕ ?` instead, which read
without any colour at all.

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

A stale answer also means white. If nothing has refreshed the dot for longer than
a poll interval — wedged AWS CLI, laptop just back from a long sleep, worker
thread died — it stops showing the last known state and says how old the answer
is. A green dot that quietly stopped updating would be worse than no dot at all,
since false confidence is the exact failure this tool exists to prevent.

### Pin your profile

If you set `profile`, the dot reports on *that* profile and nothing else — the
tool strips `AWS_ACCESS_KEY_ID` and friends from the environment it hands the CLI.
This matters: the AWS CLI ranks environment credentials **above** `AWS_PROFILE`,
so without that, stale keys sitting in your shell environment would be what got
checked. Long-lived IAM user keys showing green while your SSO session had
expired is precisely the false green you do not want. With no `profile` set the
dot is honest about it and reports "environment credentials".

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

symbols = "dots"                 # "dots" for 🟢🔴, "marks" for ✓✕
show_label = false               # put the role name next to the dot
show_account = true              # show the account id in the dropdown
show_arn = true                  # show the full ARN row
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
about what it does.

**What it never does**

- **Stores, prints, or transmits credentials.** The only files written are the
  config file you edit and a plain-text activity log.
- **Runs a shell.** It only ever executes the AWS CLI as a fixed argument list,
  with a timeout, no shell, and stdin closed. There is no config option that
  takes a command to run — the tool is read-only by design.
- **Talks to the network itself.** The only traffic is the AWS CLI reaching STS.
- **Needs sudo, or installs anything system-wide.**

**Worth knowing**

- **`show_expiry` costs one extra call.** The expiry countdown comes from
  `aws configure export-credentials`, whose output contains live credential
  material. It is parsed in memory for the `Expiration` field only and never
  logged or written to disk. Set `show_expiry = false` and the tool never sees
  credential material at all.
- **`aws_cli_path` is an escape hatch, and it executes what you point it at.**
  It is deliberately not restricted to a safe character set, because it has to be
  able to name any path on your disk. Anyone who can write your config file can
  therefore get code execution as you, persistently, via the launch agent. That
  is a real consideration but not a meaningful escalation: anyone who can write
  to your home directory can already edit this tool's source, or your shell
  profile. Leave the option unset and CLI discovery is `PATH` plus the usual
  Homebrew locations.
- **Screen sharing.** The dropdown is one click from your account id, and the ARN
  row also carries your session name, which is often your username. Since the
  whole point of the dot is to reassure you *while presenting*, set
  `show_arn = false` and `show_account = false` if your audience should not see
  those. The dot itself reveals nothing.
- **Dependencies are pinned.** `uv.lock` records exact versions with SHA-256
  hashes, and `install.sh` uses `uv sync --frozen`, so an install today resolves
  to the same reviewed packages as one last month. The no-uv fallback path
  resolves fresh — install [uv](https://docs.astral.sh/uv/) to get the lock.
- **The log** (`~/Library/Logs/little-green-dot.log`) records config problems,
  stale-check notices, and unexpected errors — no secrets. It sits inside
  `~/Library/Logs`, which macOS keeps at mode `700`. It is not rotated, though it
  only grows when something is going wrong.
- **The config file** is created mode `644` and holds no secrets (a profile name
  and a region). Note that macOS home directories are world-readable by default,
  so other local accounts on the machine can read it.
- **The launch agent** stores no secrets — just a `PATH` and this directory. Put
  no credentials in it.

**What it is not.** It is not a security control and makes no attempt to resist a
local attacker who already has your user account. It answers one question —
"would an AWS call work right now?" — and tries hard not to lie about it. A green
dot means STS answered a moment ago; it says nothing about whether the role has
the permissions you need.

## How it fits together

| File | Role |
| --- | --- |
| `little_green_dot/aws.py` | The credential check. No UI, no dependencies. |
| `little_green_dot/config.py` | Loading and validating the TOML config. |
| `little_green_dot/app.py` | The menu bar app and its polling thread. |
| `little_green_dot/cli.py` | Argument parsing and the `--once` modes. |
| `install.sh` / `uninstall.sh` | Launch agent setup and removal. |
| `uv.lock` | Exact pinned dependencies, with hashes. |

The check runs on a worker thread and hands results to the main thread through a
queue, so a slow or hanging AWS call can't freeze the menu bar.

```bash
uv run --extra dev pytest   # stubs the CLI, so it needs no AWS credentials
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
