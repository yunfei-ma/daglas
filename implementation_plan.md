# Implementation Plan — Dagläs

## Dependency graph

```
config ──────────────────────────────────────────────────► (used by all)
    │
    ├──► context_fetcher ──► context_pool ──┐
    │                                        │
    └──► lesson ─────────────────────────────┤
         ├── generator ──► llm               │
         ├── formatter                       │
         └── prompts/                        │
                                              ▼
                                    email_sender ──► subscriber_store
                                              │
                                              ▼
                                        scheduler

    (inbound, independent pipeline)
    imap ──► email_receiver ──► email_queue ──► email_processor ──► subscriber_store
                                              │
                                              └──► (archive namespace)
```

## Phase 1 — Foundation (done)

| Task | Files | Status |
|---|---|---|
| Project scaffold | `daglas/__init__.py`, `requirements.txt`, `run.py` | Done |
| Config module | `daglas/config.py`, `daglas/config_default.yaml`, `tests/test_config.py` | Done |
| Architecture doc | `AGENTS.md` (purpose, diagrams, module boundaries, conventions) | Done |
| Coding standards | `.skills/coding_review.md` | Done |
| opencode config | `opencode.json` (loads AGENTS.md + coding_review.md) | Done |
| Task: config design | `tasks/config_module.md` | Done |

### Config module — what it ships

- `DaglasConfig` dataclass with typed fields and hardcoded defaults as safety fallback.
- `load_config()` — if `config.yaml` exists, load it directly; if missing, bootstrap from `daglas/config_default.yaml`.
- Module-level `config` singleton populated by `run.py`.
- `sources: list[dict]` field for context fetcher.
- IMAP config fields (`imap_host`, `imap_port`, `imap_user`, `imap_password`) for email receiver.

## Phase 2 — Content pipeline (done)

### Task 2a: ContextFetcher

| Item | Detail |
|---|---|
| **Design doc** | `tasks/context_fetcher.md` — done |
| **Implementation** | `daglas/context_fetcher.py`, `tests/test_context_fetcher.py` — done |
| **Depends on** | Config module (reads `sources` list) |
| **Pipeline** | `Discover → Crawl → Extract → Deduplicate → Store` |
| **Discovery** | Parse sitemaps (flat + index) for article URLs |
| **Extraction** | `trafilatura` for article body, title, date, author, language |
| **Fallback** | BeautifulSoup `<article>` / `<main>` extraction |
| **Dedup** | By URL within a single run |
| **Config** | `sources` list in `config.yaml` — each entry has `name` + `sitemap` URL |
| **Output** | `Article` dataclass → JSON Lines to ContextPool |

### Task 2b: ContextPool

| Item | Detail |
|---|---|
| **Files** | `daglas/context_pool.py`, `tests/test_context_pool.py` — done |
| **Depends on** | Config module (reads `data_dir`) |
| **Storage** | JSON Lines file (`data/<date>.jsonl`) — one `Article` JSON per line |
| **API** | `store_articles(articles)`, `retrieve_articles() → list[dict]`, `clear()` |
| **Date partitioning** | Creates a new file per day automatically |

## Phase 3 — Lesson module (done)

### Task 3a: LLM abstraction

| Item | Detail |
|---|---|
| **Files** | `daglas/lesson/llm.py`, `tests/lesson/test_llm.py` — done |
| **Depends on** | Config module (reads `llm_endpoint`, `llm_model`) |
| **Backends** | ollama (default), mlx, llama.cpp — pluggable via `LlmProvider` protocol |
| **API** | `prompt(system: str, user: str) → str` |
| **Config** | `llm_endpoint: http://localhost:11434/v1` (ollama-compatible) |

### Task 3b: Generator

| Item | Detail |
|---|---|
| **Files** | `daglas/lesson/generator.py`, `tests/lesson/test_generator.py` — done |
| **Depends on** | ContextPool, LLM, Config, prompts/ |
| **Flow** | Retrieve articles from pool → build prompt with templates → call LLM → parse structured lesson |
| **Context truncation** | If article text exceeds `max_context_length`, truncate/summarize before prompting |

### Task 3c: Formatter

| Item | Detail |
|---|---|
| **Files** | `daglas/lesson/formatter.py`, `tests/lesson/test_formatter.py` — done |
| **Depends on** | Config module (reads `lesson_level`) |
| **Output** | `Email` dataclass with `subject`, `html_body`, `text_body` |
| **Content** | Vocabulary list, grammar point, example sentences, short exercise |

## Phase 4 — Delivery (done)

### Task 4a: SubscriberStore

| Item | Detail |
|---|---|
| **Files** | `daglas/subscriber_store.py`, `tests/test_subscriber_store.py` — done |
| **Storage** | Flat file (`subscribers.txt`, one email per line) |
| **API** | `list() → list[str]`, `add(email)`, `remove(email)` |

### Task 4b: EmailSender

| Item | Detail |
|---|---|
| **Files** | `daglas/email_sender.py`, `tests/test_email_sender.py` — done |
| **Depends on** | Config (SMTP settings), SubscriberStore, Formatter (Email output) |
| **Transport** | smtplib (stdlib) |
| **Config** | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `from_address` |

## Phase 4c — Inbound Email (in progress)

**Architecture**: `IMAP → EmailReceiver (pure sensor) → EmailQueue (JSONL) → EmailProcessor (classify + dispatch) → SubscriberStore`

### Task 4c-i: EmailQueue (built)

| Item | Detail |
|---|---|
| **Design doc** | `tasks/email_queue.md` — done |
| **Implementation** | `daglas/email_queue.py` — done |
| **Tests** | `tests/test_email_queue.py` (9 tests) — done |
| **Depends on** | Config module (reads `data_dir`) |
| **Storage** | JSONL per namespace per date: `data/email_queue/<namespace>/<date>.jsonl` |
| **API** | `push(namespace, RawEmail)`, `pop(namespace) → RawEmail | None`, `drain(namespace) → list[RawEmail]` |
| **Notification** | Listener pattern via `on_push(namespace, callback)` — avoids circular import with EmailProcessor |

### Task 4c-ii: EmailProcessor (built)

| Item | Detail |
|---|---|
| **Design doc** | `tasks/email_processor.md` — done |
| **Implementation** | `daglas/email_processor.py` — done |
| **Tests** | `tests/test_email_processor.py` (11 tests) — done |
| **Depends on** | EmailQueue, SubscriberStore |
| **Classification** | Substring match on subject + body against configured patterns per action |
| **Actor model** | `register(action, callable, patterns?)` — actors receive `(sender, subject, body)` |
| **Default actors** | `subscribe` → `SubscriberStore.add`, `unsubscribe` → `SubscriberStore.remove`, `unknown` → archive |
| **Registration order** | "unsubscribe" before "subscribe" — when both patterns match, unsubscribe wins |

### Task 4c-iii: EmailReceiver (refactored + lifecycle)

| Item | Detail |
|---|---|
| **Design doc** | `tasks/email_receiver.md` — updated (pure sensor + lifecycle) |
| **Implementation** | `daglas/email_receiver.py` — **refactored + start/stop** |
| **Tests** | `tests/test_email_receiver.py` (11 tests) — **rewritten + lifecycle tests** |
| **What changed** | Pure sensor (no classification) + start/stop lifecycle with `is_running` property, background thread, state machine |
| **Verification** | `scripts/email_receiver_verify.py` — still valid (tests IMAP connection) |

#### Refactoring diff

| Aspect | Old | New |
|---|---|---|
| Constructor param | `store` (SubscriberStore) | `queue` (EmailQueue) + `_stop_event`, `_thread` |
| Classification | Inline `"subscribe"`/`"unsubscribe"` matching | None — push raw, no inspection |
| Return type | `SubscriptionResult` | `int` (count of pushed emails) |
| Message handler | Classifies + calls store.add/remove | Pushes `RawEmail` to queue, marks `\Seen` |
| Test approach | Verify subscriber list side effects | Verify `EmailQueue.push` calls with `RawEmail` data |
| Lifecycle control | None (blocking `run_loop` only) | `start()` / `stop()` / `is_running` via threading |
| State diagram | None | Mermaid state machine in task doc (Stopped → Running → Stopped) |

## Phase 5 — Automation (not started)

### Task 5: Scheduler

| Item | Detail |
|---|---|
| **Design doc** | `tasks/scheduler.md` — **not created** |
| **Implementation** | `daglas/scheduler.py`, `launchd/daglas.plist` — **not built** |
| **Depends on** | All modules |
| **Flow** | `run_fetch()` → `run_pipeline()` → `send_email()` |
| **Trigger** | launchd plist (macOS native) or crontab |
| **Install** | Documented single command (`launchctl load ...`) |

## Phase 6 — Polish (partial)

- `run.py --dry-run` flag — done
- `run.py --html` flag — done (optional, off by default)
- `run.py --fetch-only` / `--generate-only` / `--send` for debugging individual phases — done
- Logging throughout (stdlib `logging`) — partial
- Error reporting (email on failure) — not started

## Files checklist (summary)

```
daglas/
├── __init__.py                  ✓
├── config.py                    ✓
├── config_default.yaml          ✓
├── context_fetcher.py           ✓
├── context_pool.py              ✓
├── email_queue.py                ✓
├── email_processor.py            ✓
├── email_receiver.py             ✓
├── subscriber_store.py           ✓
├── email_sender.py               ✓
├── scheduler.py                  ❏  (to build)
├── config_verify.py             ❏  (verify script, to build)
├── context_fetcher_verify.py    ❏  (verify script, to build)
└── lesson/
    ├── __init__.py               ✓
    ├── generator.py              ✓
    ├── formatter.py              ✓
    └── llm.py                    ✓
prompts/                           ✓
├── system.md                     ✓
├── user.md                       ✓
tests/
├── test_config.py                ✓
├── test_context_fetcher.py       ✓
├── test_context_pool.py          ✓
├── test_email_queue.py           ✓
├── test_email_processor.py       ✓
├── test_email_receiver.py        ✓
├── test_subscriber_store.py      ✓
├── test_email_sender.py          ✓
└── lesson/
    ├── test_generator.py         ✓
    ├── test_formatter.py         ✓
    └── test_llm.py               ✓
config.yaml                        ✓
run.py                             ✓
requirements.txt                   ✓
opencode.json                      ✓
AGENTS.md                          ✓
implementation_plan.md             ✓
scripts/
└── email_receiver_verify.py      ✓
tasks/
├── config_module.md              ✓
├── context_fetcher.md            ✓
├── context_pool.md               ✓
├── lesson_llm.md                 ✓
├── lesson_generator.md           ✓
├── lesson_formatter.md           ✓
├── subscriber_store.md           ✓
├── email_sender.md               ✓
├── email_receiver.md             ✓  (updated)
├── email_queue.md                ✓  (updated)
├── email_processor.md            ✓  (updated)
└── scheduler.md                  ❏  (to create)
```
