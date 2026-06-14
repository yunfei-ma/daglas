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
                                    email_sender_queue ──► smtp
                                         ▲           ▲
                                         │           │  confirmations
                                         │           │
                                   lesson │   subscriber_store
                                         │           ▲
                                         │           │
                                         └───────────┘

    (inbound, independent pipeline)
    imap ──► email_receiver ──► email_queue ──► email_processor ──► subscriber_store
                                                                      │
                                                                      └──► email_sender_queue
```

## Recent updates

| Date | Module | What |
|---|---|---|
| 2026-06-14 | Config | Added `email_sender_queue_*` interval fields |
| 2026-06-14 | EmailQueue | Flat file storage (dropped date partitioning) |
| 2026-06-14 | EmailReceiver | Pure sensor refactor + start/stop lifecycle |

## Module status

Each module links to its engineering task doc and tracks current status.
Latest updates are appended — never overwritten — so the section doubles as a changelog.

### Config

- **Task doc**: `tasks/config_module.md`
- **Status**: `done`
- **Dependencies**: none
- **Tests**: `tests/test_config.py` (7)
- **Latest**:
  - 2026-06-10 — Initial build. `DaglasConfig` dataclass with 22 fields, `load_config()`, first-run bootstrap from `config_default.yaml`.

### ContextFetcher

- **Task doc**: `tasks/context_fetcher.md`
- **Status**: `done`
- **Dependencies**: Config
- **Tests**: `tests/test_context_fetcher.py` (11)
- **Latest**:
  - 2026-06-10 — Initial build. Sitemap discovery (flat + index), trafilatura extraction, BeautifulSoup fallback, URL dedup.

### ContextPool

- **Task doc**: `tasks/context_pool.md`
- **Status**: `done`
- **Dependencies**: Config
- **Tests**: `tests/test_context_pool.py` (4)
- **Latest**:
  - 2026-06-10 — Initial build. JSONL per date under `data/<YYYY-MM-DD>.jsonl`.

### EmailQueue

- **Task doc**: `tasks/email_queue.md`
- **Status**: `done`
- **Dependencies**: Config
- **Tests**: `tests/test_email_queue.py` (9)
- **Latest**:
  - 2026-06-14 — Storage switched from date-partitioned (`data/email_queue/<ns>/<date>.jsonl`) to flat file (`data/email_queue/<ns>.jsonl`). Added `queued_at` timestamp. (See task doc Discussion.)
  - 2026-06-13 — Initial build. `RawEmail` dataclass, `push`/`pop`/`drain`, listener pattern via `on_push`.

### EmailReceiver

- **Task doc**: `tasks/email_receiver.md`
- **Status**: `done`
- **Dependencies**: Config, EmailQueue
- **Tests**: `tests/test_email_receiver.py` (14)
- **Verification**: `scripts/email_receiver_verify.py`
- **Latest**:
  - 2026-06-14 — Refactored to pure sensor. Stripped classification logic. Added `start()`/`stop()` lifecycle with `is_running` property, background thread, state machine.
  - 2026-06-10 — Initial build. IMAP polling, UNSEEN detection, raw email push to EmailQueue.

### EmailProcessor

- **Task doc**: `tasks/email_processor.md`
- **Status**: `done`
- **Dependencies**: EmailQueue
- **Tests**: `tests/test_email_processor.py` (8)
- **Latest**:
  - 2026-06-14 — Simplified to blind notification hub. Removed `ClassificationResult`. Listeners receive `(sender, subject, body)`. Registers itself on `EmailQueue.on_push("incoming")`.

### EmailSender

- **Task doc**: `tasks/email_sender.md`
- **Status**: `done` (now internal to EmailSenderQueue)
- **Dependencies**: Config
- **Tests**: `tests/test_email_sender.py` (8)
- **Latest**:
  - 2026-06-14 — Made internal. `SmtpSender` is now created and owned by `EmailSenderQueue`. No module outside `email_sender_queue.py` imports it directly.

### EmailSenderQueue

- **Task doc**: `tasks/email_sender_queue.md`
- **Status**: `done`
- **Dependencies**: Config, EmailSender (internal)
- **Tests**: `tests/test_email_sender_queue.py` (13)
- **Latest**:
  - 2026-06-14 — Initial build. `SendRequest` dataclass, two JSONL files (immediate + scheduled), background thread dispatch, configurable poll intervals.

### Generator

- **Task doc**: `tasks/lesson_generator.md`
- **Status**: `done`
- **Dependencies**: ContextPool, LLM, Config, prompts/
- **Tests**: `tests/lesson/test_generator.py` (5)
- **Latest**:
  - 2026-06-10 — Initial build. Prompt assembly from `prompts/system.md` and `prompts/user.md`, context truncation, dry-run mode.

### Formatter

- **Task doc**: `tasks/lesson_formatter.md`
- **Status**: `done`
- **Dependencies**: Config
- **Tests**: `tests/lesson/test_formatter.py` (4)
- **Latest**:
  - 2026-06-10 — Initial build. `Email` dataclass with subject/html/text, markdown-to-HTML conversion.

### LLM

- **Task doc**: `tasks/lesson_llm.md`
- **Status**: `done`
- **Dependencies**: Config
- **Tests**: `tests/lesson/test_llm.py` (7)
- **Latest**:
  - 2026-06-10 — Initial build. `LlmProvider` protocol, `OllamaProvider`/`MlxProvider`/`LlamaCppProvider` implementations, `create_provider()` factory.

### Scheduler

- **Task doc**: `tasks/scheduler.md` — not created
- **Status**: `not_started`
- **Dependencies**: all modules
- **Tests**: none
- **Latest**:
  - (not started)

### SubscriberStore

- **Task doc**: `tasks/subscriber_store.md`
- **Status**: `done`
- **Dependencies**: Config, EmailSenderQueue, LLM (optional)
- **Tests**: `tests/test_subscriber_store.py` (21)
- **Verification**: `scripts/subscriber_store_verify.py`
- **Latest**:
  - 2026-06-14 — Refactored to use `EmailSenderQueue` instead of `SmtpSender`. Added bilingual welcome/unsubscribe templates. Added name extraction via LLM. Per-user notes with `Email:` + `Name:` headers.
  - 2026-06-10 — Initial build. Flat-file subscriber list, basic add/remove.

## Cross-cutting items

These are not standalone modules but span the whole project.

### run.py

- **Status**: `done`
- **Tests**: none (integration tested manually)
- **Latest**:
  - 2026-06-14 — Wired `EmailSenderQueue` lifecycle (start/stop), inbound pipeline wiring, `_resolve_send_time`, `_queue_lesson`, `--send` flag, `--max-articles` flag.

### Prompts

- **Status**: `done`
- **Files**: `prompts/system.md`, `prompts/user.md`, `prompts/name_extraction_system.md`, `prompts/name_extraction_user.md`
- **Latest**:
  - 2026-06-14 — Added name extraction prompts.
  - 2026-06-10 — Initial lesson generation prompts.

### Integration verification scripts

- **Status**: `partial`
- **Files**: `scripts/email_receiver_verify.py` (done), `scripts/subscriber_store_verify.py` (done), `scripts/config_verify.py` (todo), `scripts/context_fetcher_verify.py` (todo)
- **Latest**:
  - 2026-06-14 — `subscriber_store_verify.py` added. Exercises full subscribe/unsubscribe flow with real SmtpSender.

### Logging

- **Status**: `partial`
- **Latest**:
  - 2026-06-10 — Stdlib `logging` throughout, httpx/urllib3 muted.

### Error reporting

- **Status**: `not_started`
- **Latest**:
  - (not started)

## Files checklist

```
daglas/
├── __init__.py                  ✓
├── config.py                    ✓
├── config_default.yaml          ✓
├── context_fetcher.py           ✓
├── context_pool.py              ✓
├── email_queue.py                ✓
├── email_processor.py            ✓  (simplified)
├── email_receiver.py             ✓  (start/stop lifecycle)
├── email_sender_queue.py         ✓
├── subscriber_store.py           ✓  (bilingual templates, email headers, name extraction)
├── email_sender.py               ✓  (internal to EmailSenderQueue)
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
├── name_extraction_system.md     ✓
├── name_extraction_user.md       ✓
tests/
├── test_config.py                ✓
├── test_context_fetcher.py       ✓
├── test_context_pool.py          ✓
├── test_email_queue.py           ✓
├── test_email_processor.py       ✓
├── test_email_receiver.py        ✓
├── test_subscriber_store.py      ✓
├── test_email_sender.py          ✓
├── test_email_sender_queue.py    ✓  (13 tests)
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
├── email_receiver_verify.py      ✓
└── subscriber_store_verify.py    ✓  (251 lines)
tasks/
├── config_module.md              ✓
├── context_fetcher.md            ✓
├── context_pool.md               ✓
├── lesson_llm.md                 ✓
├── lesson_generator.md           ✓
├── lesson_formatter.md           ✓  (updated)
├── subscriber_store.md           ✓  (updated)
├── email_sender.md               ✓  (updated — SmtpSender is now internal)
├── email_sender_queue.md         ✓  (created)
├── email_receiver.md             ✓  (updated)
├── email_queue.md                ✓  (updated)
├── email_processor.md            ✓  (simplified)
├── run.md                        ✓  (updated)
└── scheduler.md                  ❏  (to create)
```
