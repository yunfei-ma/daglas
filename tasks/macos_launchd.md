# macOS Launchd — Process Supervision & Scheduling

## 1. Purpose

Use macOS `launchd` for two distinct responsibilities:

- **Outbound pipeline scheduling** — fire `run.py --interval` every 30 minutes
  via `StartInterval`. The pipeline checks wall time against config
  (`fetch_time`, `send_time`) to decide whether to generate. Max 30-minute
  delay after the configured time, regardless of sleep/wake cycles.
- **Persistent process supervision** — keep `run.py` alive for inbound email
  polling and email sender dispatch (fast timers, small monotonic drift
  is acceptable).

Both share the same data files (JSONL queue, context pool, subscriber store).

## 2. Background — macOS launchd and sleep/wake

launchd is macOS's native service/process manager (equivalent to systemd on
Linux). It is PID 1 on macOS and is responsible for booting the system and
managing daemons and agents.

### Key concepts

- **plist** — XML property list file that describes a service. Lives in
  `~/Library/LaunchAgents/` for per-user agents or `/Library/LaunchDaemons/`
  for system-wide daemons.
- **RunAtLoad** — start the process as soon as the plist is loaded.
- **KeepAlive** — restart the process if it exits or crashes.
- **StartInterval** — run the process every N seconds. launchd tracks this
  using **wall time** — it fires at the correct interval regardless of system
  sleep, because launchd (PID 1) stays awake.

### Why cron and Python timers don't work here

| Mechanism | Sleep behavior | Suitability |
|---|---|---|
| `cron` | Missed jobs are skipped — not run on wake | ❌ Not suitable |
| `time.sleep`, `Event.wait`, etc. | Process suspended — monotonic clock pauses | ❌ Drifts by full sleep duration |
| `launchd StartInterval` | Fires at correct wall time | ✅ Correct for daily scheduling |

### Sleep/wake behavior

When macOS sleeps, **user processes are suspended in memory** — they stop
executing entirely. On wake, they resume from where they were frozen.

Python's `time.sleep()`, `threading.Event.wait()`, and `select.select()` all
use `CLOCK_MONOTONIC`. Monotonic time counts only the actual runtime — it
does **not** advance while the system is asleep. A `wait(timeout=86400)` call
will sleep for 86400 seconds of runtime, which can be days of wall time if
the laptop sleeps frequently.

**Consequence:** Any relative timer that relies on monotonic time will drift
relative to wall clock across sleep cycles. Scheduling must either:
- Use launchd `StartInterval` (launchd stays awake and tracks wall time)
- Poll on a short interval and check `datetime.now()` (but each poll interval
  is still inflated by sleep duration — see discussion above)

For the daily lesson (which must arrive on the right day), launchd is the
reliable choice.

### launchctl commands

| Command | What it does |
|---|---|
| `launchctl load ~/Library/LaunchAgents/com.daglas.runner.plist` | Register and start |
| `launchctl unload ~/Library/LaunchAgents/com.daglas.runner.plist` | Stop and unregister |
| `launchctl list \| grep daglas` | Check running status |
| `plutil -lint com.daglas.runner.plist` | Validate plist XML syntax |

## 3. Architecture — split design

### 3.1 Two plist files

| Plist label | Trigger | Runs | Responsibility |
|---|---|---|---|
| `com.daglas.outbound` | `StartInterval` 1800 | `python run.py --interval` | Daily lesson: fetch, generate, queue |
| `com.daglas.runner` | `KeepAlive` + `RunAtLoad` | `python run.py` | Inbound email polling, sender dispatch |

### 3.2 Outbound pipeline — wall-time gate

`run.py --interval` is a periodic mode fired by launchd. The
pipeline:

1. Fetches context, generates lesson, queues to sender queue JSONL
2. Uses `datetime.now(timezone.utc)` to check whether it's past `send_time`
   (or `fetch_time`). If the lesson was already generated today, skip.
3. Since launchd fires every 30 minutes, the worst-case wall-time delay from
   the configured time is 30 minutes — even if the system was sleeping.

The outbound pipeline already handles idempotency: it writes to the sender
queue, not directly to SMTP. Multiple invocations of `--interval` within the
same day will simply re-queue the same lesson, and the sender queue handles
dedup at the dispatch layer.

### 3.3 Persistent runner — fast polling

The KeepAlive `run.py` handles:

- **EmailReceiver** — polls IMAP every `email_receiver_poll_interval` (~30s).
  A few seconds of monotonic drift after sleep is harmless for IMAP polling.
- **EmailSenderQueue** — dispatches immediate/scheduled emails on configurable
  intervals (~20s/120s). Similarly tolerant of small drift.

These run hundreds of times per day. Even a 1-hour sleep only delays one
poll cycle, which is acceptable for unsubscribe confirmations and email
dispatch.

## 4. Edge cases

- **System sleeps through send_time**: launchd fires at the next 30-minute
  mark after wake. Lesson is queued with `send_at=immediate` if the wall
  time is past the original `send_time`. Max delay: 30 minutes.
- **Multiple outbound fires in one day**: pipeline checks `send_time` — if
  already past, skips re-generation. Dedup handled at sender queue.
- **Crash during outbound**: launchd restarts `KeepAlive` runner immediately;
  outbound will fire at next 30-minute interval.
- **Config change**: Run `scripts/install_launchd.py` again to regenerate
  plists. Old jobs are unloaded first.

## 5. CLI usage

```bash
# Install both plists
python scripts/install_launchd.py

# Uninstall both
python scripts/uninstall_launchd.py

# Check status
launchctl list | grep daglas

# Manual outbound (any time)
python run.py --interval
```

## 6. Unit Test Strategy

- `pytest tests/test_launchd.py`:
  - `generate_plists()` returns 2 plists with correct structure
  - Outbound plist has `StartInterval` = 1800
  - Runner plist has `KeepAlive=true` and `RunAtLoad=true`
  - `ProgramArguments` points to correct python + run.py
  - `write_plists()` produces valid XML (`plutil -lint`)

## 7. Acceptance Criteria

- `python scripts/install_launchd.py` generates 2 valid plist files and loads
  both
- `launchctl list | grep daglas` shows both `com.daglas.outbound` and
  `com.daglas.runner`
- The `com.daglas.outbound` process runs at least once every 30 minutes
- After sleep, the outbound pipeline fires within 30 minutes of wake
- The persistent runner restarts automatically if killed
- `scripts/uninstall_launchd.py` unloads and removes both plists
- `ruff check . && ruff format --check .` passes

## 8. Files checklist

```
scripts/
├── install_launchd.py              ✓  (new)
├── uninstall_launchd.py            ✓  (new)
tests/
├── test_launchd.py                 ✓  (new)
daglas/
├── pipeline.py                     △  (--interval mode already exists)
run.py                              △  (--interval flag already exists)
```

(✓ = new, △ = modified — no modifications needed beyond what already exists)

## Discussion

### 2026-06-15 — Split design: launchd outbound + persistent runner

**What changed:**
- Rewritten from single-KeepAlive design to two-plist split:
  - `com.daglas.outbound` — `StartInterval` 1800, runs `--interval`
  - `com.daglas.runner` — `KeepAlive` + `RunAtLoad`, persistent
- Rationale: Python monotonic timers drift across sleep/wake cycles. launchd
  (PID 1) stays awake and tracks wall time correctly.
- Outbound pipeline checks `datetime.now(timezone.utc)` against config to
  decide whether to generate. Max 30 min delay after configured time.
- Persistent runner still uses monotonic timers — drift is acceptable for
  IMAP polling and email dispatch (hundreds of cycles per day).
- Sleep/wake behaviour documented as background knowledge.

**Impact on implementation plan:**
- Launchd task doc updated to reflect split design.
- No changes needed to existing code — `--interval` flag and `_run_interval()`
  already exist on both pipeline and run.py.

**TODO actions:**
- [x] Rewrite task doc with split design and sleep/wake background.
- [ ] Write `scripts/install_launchd.py` — generate 2 plists, load both.
- [ ] Write `scripts/uninstall_launchd.py`.
- [ ] Write `tests/test_launchd.py`.
- [ ] Test: sleep/wake cycle, verify outbound fires within 30 min of wake.
- [ ] Test: kill persistent runner, verify `KeepAlive` restarts it.
- [ ] Run `ruff check . && ruff format --check .`.
- [ ] Run full test suite `pytest`.

### 2026-06-15 — Single-KeepAlive design (archived)

Originally proposed as a single plist with `KeepAlive` + `RunAtLoad`, relying
on a short poll loop to compensate for sleep. Replaced by the split design
above because monotonic drift inflates every poll interval by the full sleep
duration, not just one interval.
