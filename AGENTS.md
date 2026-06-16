# Dagläs — Daily Swedish Lessons

## Purpose

Generate one structured Swedish lesson email every morning, grounded in
real-world, current context. Runs on a Mac Mini using a local LLM.

## Architecture (modular pipeline)

Each module is a standalone script/file with one clear responsibility.
Modules are independently replaceable/extensible.

```mermaid
classDiagram
    class Config {
        +load()
        +get(key) any
    }

    class ContextFetcher {
        +fetch()
    }
    class ContextPool {
        +store(text)
        +retrieve() string
    }
    class Lesson {
        +generate(context) Email
    }
    class EmailSender {
        +send(email, recipients)
    }
    class SubscriberStore {
        +list() list~string~
    }

    ContextFetcher       --> Config         : reads
    ContextFetcher       --> ContextPool    : writes
    Lesson               --> Config         : reads
    Lesson               --> ContextPool    : reads
    EmailSender          --> Config         : reads
    EmailSender          --> SubscriberStore : reads

```mermaid
sequenceDiagram
    participant Run as run.py
    participant Fetch as ContextFetcher
    participant Pool as ContextPool
    participant Lesson as Lesson
    participant Sender as EmailSender
    participant Subs as SubscriberStore

    Note over Run,Subs: Startup — run outbound pipeline once
    Run->>Fetch: fetch_context()
    Fetch->>Pool: store(raw_context)
    Run->>Lesson: generate_lesson()
    Lesson->>Pool: retrieve()
    alt context > MAX_CONTEXT_LENGTH
        Lesson->>Lesson: truncate/summarize
    end
    Lesson->>Lesson: prompt LLM
    Lesson->>Lesson: format email
    Lesson-->>Run: Email
    Run->>Sender: queue(email, send_at=cfg.send_time)

    Note over Run,Subs: Persistent loop — stay alive
    Run->>Run: keyboard loop (Ctrl+C / q+Enter)
    Note over Fetch: daemon thread polls on timer
    Note over Sender: daemon thread dispatches on schedule
```

Module boundaries:

| Module | Responsibility |
|---|---|---|
| `config` | Load config from `.env` / `config.yaml`; single source of truth exposed to all modules |
| `context_fetcher` | Fetch from configured sources (RSS, API, scraping); pluggable per source |
| `context_pool` | Store fetched raw context for the day (file, DB, or in-memory) |
| `lesson` | **Top-level module** — produces the email lesson |
| `├── generator` | Read context from pool; prompt LLM; truncate/summarize context |
| `├── formatter` | Render lesson into email HTML/text |
| `├── llm` | Abstraction over local LLM providers (llama.cpp, ollama, mlx) |
| `└── prompts/` | Versioned prompt template files |
| `email_sender` | Dispatch the formatted lesson to subscribers |
| `email_receiver` | Poll IMAP, push raw emails to EmailQueue (no content inspection) |
| `email_queue` | Persistent namespaced JSONL queue, notify listeners on push |
| `email_processor` | Classification + dispatch hub, actor registration for actions |
| `subscriber_store` | Manage recipient list (flat file, DB, env) |

## Workflow conventions

- **LLM runs locally** — use `llama.cpp`, `ollama`, `mlx`, or similar.
  Never assume a cloud API is available or preferred.
- **Configuration** lives in environment variables or a single top-level
  `.env` / `config.yaml`. No secrets in code.
- **Testing** — tests live in `tests/` mirrored to `daglas/` structure
  (`tests/test_module_name.py`). Run all tests with `pytest`.
- **Python preferred** unless a module has a clear reason otherwise.
- **Formatting**: `ruff format`, `ruff check` before committing.
- **Context length control** — `lesson.generator` must accept a configurable
  max context length; if the fetched context exceeds it, the module
  truncates/summarizes before prompting the LLM.
- **Default beginner config** — ship a `config.yaml` (or `.env` defaults)
  with beginner-friendly values: e.g., `MAX_CONTEXT_LENGTH=500`,
  `ARTICLE_WORD_LIMIT=100`, `LESSON_LEVEL=beginner`, `VOCAB_COUNT=5`.
  These should be overridable.

## Commands

```bash
# run full pipeline (dry-run, no email send)
python run.py --dry-run

# test all modules
pytest

# lint and format
ruff check . && ruff format .

# generate a single lesson manually
python -m daglas.lesson
```

## Task files

Detailed engineering design & implementation specs for individual modules
live in `tasks/<module_name>.md`. Each task doc covers scope, use cases,
library choices, API design, implementation plan, and unit test strategy.

**Process rule: task doc must exist before implementation begins.**
Before writing any code for a module, the agent must:
1. Check if `tasks/<module_name>.md` exists.
2. Load `.skills/project_management.md` via the `skill` tool.
3. If it does, read it and follow it.
4. If it doesn't, create it first (read existing task docs for format reference),
   get user sign-off, then implement.
5. "I'll create the task doc as I code" is not acceptable — design first, then build.
6. Every task doc **must** include a UML component diagram showing module
   relationships, interfaces, and data flow. Use the `.skills/uml_component.md`
   skill (load via `skill` tool) to generate the diagram.
7. Before writing any Mermaid diagram, load `.skills/mermaid_safety.md` and
   follow its pre-write checklist — edge labels must be plain words only (no
   `[]{}()<>#`), node IDs must be `snake_case`/`CamelCase`, and `classDef`
   must appear before node declarations.
8. Every task doc **must** end with a `## Discussion` section (see
   `.skills/project_management.md` for format). Use it to record changes,
   design decisions, and TODO actions as the doc evolves.
9. After creating or updating any task doc, update `implementation_plan.md`
   to reflect the current state (see `.skills/project_management.md`).

This rule exists to prevent agents from implementing modules without a spec,
which leads to undocumented design decisions and untestable outcomes. The
Discussion section and plan updates prevent drift between docs and code.

All current task files:
- `tasks/config_module.md` — Config module (bootstrap, load, defaults)
- `tasks/context_fetcher.md` — ContextFetcher (sitemap discovery, article extraction)
- `tasks/context_pool.md` — ContextPool (JSON Lines storage, date-partitioned)
- `tasks/lesson_llm.md` — LLM provider abstraction (ollama, mlx, llama.cpp)
- `tasks/lesson_generator.md` — Generator (prompt assembly, context truncation)
- `tasks/lesson_formatter.md` — Formatter (Email dataclass, markdown→HTML)
- `tasks/subscriber_store.md` — SubscriberStore (flat file CRUD)
- `tasks/email_receiver.md` — EmailReceiver (IMAP polling, raw push to queue)
- `tasks/email_queue.md` — EmailQueue (persistent namespaced JSONL queue)
- `tasks/email_processor.md` — EmailProcessor (classification, actor dispatch)
- `tasks/email_sender.md` — EmailSender (SMTP dispatch)

Tasks referenced via this doc can be picked up by an agent as a self-contained
implementation brief.

## Working Process

All work follows a task-doc-first workflow. Discussion happens in the task
doc, not in code.

### Process gates

1. **Design first** — propose changes by writing or updating the relevant task
   doc. Never jump to code.
2. **User approval** — wait for explicit go-ahead before touching any source
   file. Verbal proposals are fine; edits are not.
3. **Implement** — code changes only after approval.
4. **Verify** — tests, lint, format.

### Iron rule

> **Never modify source code (`.py` files, config, prompts, scripts) or delete
> any file without the user's explicit prior approval.** This includes any edit,
> write, or deletion — even if the change seems trivial or correct. You may
> read files, search, and propose changes verbally, but the actual edit must
> wait for a go-ahead.

Applies to: all `.py`, `.yaml`, `.md` (except this file), `.sh`, `.json`,
`.txt` files in the repository. Does not apply to temporary/scratch files
outside the repo.

Violating this rule is a project-process error, not a technical mistake —
revert and apologise.

## Important constraints

- Keep prompt templates versioned in a `prompts/` directory, not inline.
- Lesson format is daily Swedish reading practice: original Swedish text
  with inline English explanations. No grammar drills or exercises.
- Content source must be **current** — do not hardcode stale articles.
- Do not assume any cloud service is available (no OpenAI, no SendGrid
  unless self-hosted alternatives are ruled out).

## Quality: proof-of-work per task

Every task deliverable — at the smallest unit level — must carry **proven
work** behind every claim it makes. The following rules apply to all code,
config, docs, and tests produced by any agent working on this project.

### 1. Traceability, not invention

Every technical claim, task target, verification step, XML schema element,
config option, data type, log format, string compatibility rule, or any
other concrete detail **must be directly traceable** to one of:

- A file in this repository that the agent has **actually read** (not guessed).
- An official reference document (library docs, language spec, RFC) that the
  agent has fetched and read during the session.
- The output of a tool the agent has run (e.g., `python3 -c "import x; print(x.__version__)"`).

No details may be extrapolated, assumed, or invented from general knowledge
alone. If a source does not specify a value, format, or behaviour, the agent
must say so explicitly rather than fabricating one.

### 2. File-backed verification

Every acceptance criterion in a task doc must reference a **specific file
path** that implements or tests it. Examples:

```
Acceptance criteria:
- `pytest tests/test_config.py` passes all tests.        ✔ real file
- `ruff check daglas/` passes.                             ✔ real command
- DaglasConfig.lesson_level defaults to "beginner"         ✔ field in daglas/config.py:16
```

### 3. No phantom references

Never reference a function, class, field, file, config key, or command that
does not exist in a file the agent has read during the current session.
If a task requires a module that hasn't been built yet, the task doc must
explicitly mark it as **planned / not yet implemented** rather than
pretending it exists.

### 4. Data-type honesty

Every data type, struct field, function signature, and config schema
published in a task doc must match the **actual source code** character for
character. If the implementation drifts from the design doc, the design doc
must be updated before the next task references it.

### 5. Version and compatibility anchoring

When choosing a library or tool, verify its availability on the target
platform by running a concrete check (`import` or `--version` command) and
record the result. Never assume a library is present because it is popular.
When pinning a version constraint in `requirements.txt`, confirm that the
constraint is compatible with what is actually installed.

### 6. Proof of Working — real integration verification

Unit tests with mocks are necessary but **not sufficient** for modules whose
sole purpose is I/O with an external system. Every module that connects to a
real service must carry a documented, repeatable integration test against the
actual system.

**Design rule (task docs):** Every task doc for an I/O module must include a
"Real integration verification" subsection under "Unit Test Strategy" that
specifies:

- What external system to connect to (and its config key)
- A one-shot script or command that exercises the real connection
- The acceptance criterion (what output proves it works)
- How to interpret failures

For non-I/O modules (pure data transformation, algorithmic), this section is
optional but encouraged if the module depends on a runtime service.

**Implementation rule (code):** Before marking an I/O module as complete, the
agent must:

1. Ensure unit tests pass (`pytest tests/test_<module>.py`).
2. Write and run a standalone integration script that connects to the real
   service, exercises the primary use case, and prints the result.
3. Include the script's output in the deliverable as proof.

The integration script lives in `scripts/<module>_verify.py` and is committed
to the repository so it can be re-run for regression checks.

**Rationale:** Mocks verify the module handles the protocol correctly when the
protocol works. Only a real connection proves the credentials, network, server
compatibility, and data format all function together. Without this step, a
module is a plausible fiction — passing all tests but failing on first real use.


