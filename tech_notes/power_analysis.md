# Power Consumption Analysis — Persistent Loop

## Context

`run.py` by default enters a persistent loop (keeps the main thread alive for
continuous IMAP polling and EmailSenderQueue dispatch). This note quantifies
the power cost of keeping the process alive vs a cron-based one-shot approach.

## Threads and their wait mechanisms

| Thread | Wait mechanism | Active CPU per iteration | Iterations/day | Active CPU/day |
|--------|---------------|-------------------------|----------------|----------------|
| Main loop | `input()` — kernel block | 0 | — | **0** |
| Immediate poll | `Event.wait(timeout=20)` | ~0.001s (stat+read empty file) | 4320 | **~4s** |
| Scheduled poll | `Event.wait(timeout=300)` | ~0.001s (same) | 288 | **~0.3s** |
| IMAP poll | `Event.wait(timeout=300)` | ~0.1-0.5s (TCP+TLS+SEARCH) | 288 | **~30-150s** |
| Email dispatch | triggered on demand | ~0.5-2s per email (SMTP) | 0-5 | **0-10s** |

Total active CPU per day: **~35-165 seconds** — most of which is the IMAP
poll's TLS handshake (kernel/userspace for encryption).

## Measured overhead

30s of `Event.wait(timeout=1)` calls in 30 threads:
- User CPU: **0.000s**
- Sys CPU: **0.000s**
- CPU utilisation: **<0.0001%**

`threading.Event.wait(timeout=N)` uses `pthread_cond_timedwait` on macOS —
a kernel-level interruptible sleep. The thread is fully descheduled; the
kernel wakes it only when the timeout expires or the event is set. There is
no busy-waiting, no spinlock, no polling overhead.

## Power comparison

Based on a Mac Mini M-series idling at ~7W at the wall:

| Scenario | CPU active/day | Process RAM | Extra power over idle |
|----------|---------------|-------------|----------------------|
| No process (cron) | 0 | 0 | **0** |
| Persistent loop | ~35-165s | ~10 MB | **sub-milliwatt** |
| Full pipeline run | ~30-60s at 30-50% CPU | — | **~0.1 Wh** |

The persistent loop adds **less than 0.01W** on average. The baseline 7W idle
of the Mac Mini itself — plus the network interface staying active for IMAP
polling — already dwarfs this.

## Conclusion

Power is not a meaningful concern. The process spends >99.9% of its time in
interruptible sleep. If you still want a lightweight alternative:

```
python3 run.py --one-shot   # run pipeline once and exit
```
