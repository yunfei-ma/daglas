# Dagläs — Daily Swedish Lessons

Generate one structured Swedish lesson email every morning, grounded in real-world, current context. Runs locally via a local LLM (ollama, mlx, llama.cpp).

## Architecture

Modular pipeline with independent, replaceable modules:

```
+---------------------+     +---------------------+     +---------------------+
|   Input Channels    | --> |     Processing      | --> |       Output        |
+---------------------+     +---------------------+     +---------------------+
| Web Fetcher         |     | Web Context (pool)  |     | Email Sender        |
| Email Receiver      |     | Email Queue         |     +---------------------+
+---------------------+     | Email Processor     |
                            | Subscriber Store    |
                            | +-----------------+ |
                            | |     Lesson      | |
                            | | Generator       | |
                            | | LLM             | |
                            | | Formatter       | |
                            | | Lesson Queue    | |
                            | +-----------------+ |
                            +---------------------+

+---------------------------------------------------------------------+
|                           Configuration                                |
+---------------------------------------------------------------------+
```

| Area | Module | Responsibility |
|---|---|---|
| **Configuration** | `config` | Load from `config.yaml`; single source of truth |
| **Input Channels** | `context_fetcher` | Fetch from RSS/sitemap sources, extract article text |
| | `email_receiver` | Poll IMAP, push raw emails to queue |
| **Processing** | `context_pool` | Store/retrieve fetched articles (JSON Lines per day) |
| | `email_queue` | Persistent JSONL queue with namespacing |
| | `email_processor` | Classify + dispatch incoming emails to actions |
| | `subscriber_store` | Manage recipient list |
| | `lesson.generator` | Prompt LLM with article context to produce a lesson |
| | `lesson.llm` | Abstraction over local LLM providers |
| | `lesson.formatter` | Render lesson into email HTML/text |
| | `lesson.queue` | Queue ready-to-send formatted lessons |
| **Output** | `email_sender` | SMTP dispatch to subscribers |

## Requirements

- Python 3.10+
- A local LLM server (e.g. [ollama](https://ollama.com), mlx, llama.cpp)

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp daglas/config_default.yaml config.yaml
# Edit config.yaml with your LLM endpoint, SMTP settings, etc.

# Add subscribers
echo "you@example.com" > subscribers.txt
```

## Usage

```bash
# Run full pipeline (no email send)
python run.py --dry-run

# Generate a lesson from today's context
python run.py --generate-only

# Fetch context only
python run.py --fetch-only

# Full pipeline with send
python run.py --send

# With HTML output
python run.py --html --send
```

## Testing

```bash
pytest

# Lint and format
ruff check . && ruff format .
```

## Configuration

All settings in `config.yaml`. Key options:

| Key | Default | Description |
|---|---|---|
| `llm_endpoint` | `http://localhost:11434/v1` | LLM API endpoint |
| `llm_model` | `gemma4:latest` | Model name |
| `max_context_length` | `500` | Max tokens for article context |
| `article_word_limit` | `100` | Word limit per article |
| `lesson_level` | `beginner` | Target level |
| `vocab_count` | `5` | Words per vocabulary list |
| `sources` | `[{svt}]` | Content sources (name + sitemap URL) |
| `smtp_host` | — | SMTP server for sending |
| `imap_host` | — | IMAP server for receiving |

## Project structure

```
daglas/              # Core modules
├── config.py        # Config loading
├── context_fetcher.py
├── context_pool.py
├── email_sender.py
├── email_receiver.py
├── email_queue.py
├── email_processor.py
├── subscriber_store.py
└── lesson/          # Lesson generation
    ├── generator.py
    ├── formatter.py
    └── llm.py
prompts/             # LLM prompt templates
tests/               # Tests mirrored to daglas/ structure
tasks/               # Module design docs
scripts/             # Integration verification scripts
run.py               # CLI entry point
```
