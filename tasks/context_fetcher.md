# ContextFetcher Module — Engineering Design & Implementation Task

## 1. Purpose

Discover, crawl, and extract full-article content from Swedish-language websites every morning. Store structured article data into `ContextPool` so the lesson module has current real-world material to work from.

The module provides a daemon lifecycle (`start()` / `stop()` / `_run()`) matching the pattern of `EmailReceiver` and `EmailSenderQueue`. The daemon thread sleeps until the configured `fetch_time`, executes the fetch pipeline, then sleeps until the next day's fetch_time.

## 2. Component Diagram

```mermaid
graph LR
    classDef core    fill:#bbe5d5,stroke:#0F6E56,color:#085041
    classDef store   fill:#cadbea,stroke:#185FA5,color:#0C447C
    classDef external fill:#f1dfc0,stroke:#BA7517,color:#633806

    Runner[run.py]:::core
    Daemon[ContextFetcherDaemon]:::core
    Site{{svt.se / dn.se}}:::external
    Pool[(ContextPool)]:::store
    Config[DaglasConfig]:::core

    Runner -->|start / stop| Daemon
    Daemon -->|sleeps until fetch_time| Daemon
    Daemon -->|call| Fetcher
    Fetcher[fetch_context]:::core
    Config -->|source list| Fetcher
    Fetcher -->|sitemap URL| Site
    Site -->|article HTML| Fetcher
    Fetcher -->|articles| Pool
```

## 3. Pipeline

```
Parse sitemap → Filter by age → Crawl → Extract → Deduplicate → Store
```

| Stage | What it does |
|---|---|
| **Parse sitemap** | Fetch the configured flat sitemap URL, extract each `<url>` entry: `<loc>` (required), best-available date (try `<news:publication_date>` → `<lastmod>` → `None`), and title if present |
| **Filter by age** | Drop entries whose date is older than `max_age_hours` |
| **Crawl** | HTTP-fetch each remaining article page |
| **Extract** | Parse HTML into structured fields: title, body, dates, author, metadata |
| **Deduplicate** | Skip already-seen URLs (across the current run, and vs. previously stored) |
| **Store** | Write deduplicated articles as structured data to `ContextPool` |

## 3. Scope (MVP)

- **Content channels**: flat sitemap parsing (config must point to a `<urlset>` sitemap, not a `<sitemapindex>`).
- **Extraction**: full-article content, title, publish date, author, language.
- **Age-based filtering**: configurable `max_age_hours` per source skips stale entries before HTTP fetch.
- **Deduplication**: by URL within a single run.
- **Error handling**: one failing source does not block others; per-article timeout.
- **Output**: structured `Article` objects serialized as JSON Lines to `ContextPool`.

Non-goals: API authentication, JavaScript rendering, screenshot capture, diff-based refetching, multi-language detection beyond what the article metadata provides.

## 4. Use Cases

| UC | Description |
|---|---|
| UC1 | **Discover from sitemap** — fetch sitemap, extract article URLs, crawl and extract each |
| UC2 | **Graceful degradation** — one article URL fails (timeout, 404); remaining articles still processed |
| UC3 | **Deduplicate** — repeated URL within a run is skipped |
| UC4 | **No sources configured** — no-op, nothing stored |
| UC5 | **All sources fail** — no articles stored; pool retains previous day's content |
| UC6 | **Daemon lifecycle** — `start()` launches a daemon thread that sleeps until `fetch_time`, runs the fetch pipeline, then sleeps until the next day's `fetch_time`. `stop()` signals the thread to exit cleanly. |

## 5. Python Libraries

| Library | Why |
|---|---|
| `httpx` | Async-capable HTTP client with connection pooling and timeout support |
| `beautifulsoup4` | HTML/XML parsing for sitemaps and fallback content extraction |
| `lxml` | Fast XML/HTML parser backend for BeautifulSoup |
| `trafilatura` | Reliable article content extraction (title, body, date, author, language) |
| Standard `json` | Serialize articles as JSON Lines for storage |

Dependency spec (add to `requirements.txt`):

```
httpx>=0.28
beautifulsoup4>=4.12
lxml>=5.0
trafilatura>=2.0
```

## 6. Interface

### Location: `daglas/context_fetcher.py`

```python
from dataclasses import dataclass, field


@dataclass
class SitemapEntry:
    """One article discovered from a flat sitemap before its HTML is fetched.

    Carries metadata from the sitemap XML (<news:news> or <lastmod>) so the
    pipeline can filter by age and sort by recency without downloading the
    article page.
    """
    url: str
    publish_date: str | None = None
    title: str = ""


@dataclass
class Article:
    publish_date: str | None = None
    url: str = ""
    title: str = ""
    body: str = ""
    source: str = ""
    updated_date: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    author: str | None = None
    language: str | None = None


@dataclass
class FetchResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_sitemap_entries(sitemap_url: str, client: httpx.Client) -> list[SitemapEntry]:
    """Fetch a flat sitemap and return structured entries.

    Extracts <loc>, the best available date
    (<news:publication_date> > <lastmod>), and title
    (<news:title>) from each <url> entry.

    Raises ValueError if the response is a sitemap index
    (<sitemapindex>) — config must point to a flat sitemap.
    """
    ...


def extract_article(url: str, html: str) -> Article:
    """Parse article HTML into a structured Article object."""
    ...


def fetch_context(
    source_configs: list[dict],
    pool: ContextPool,
    *,
    user_agent: str = "daglas/1.0",
    max_articles: int = 0,
    max_age_hours: int = 0,
) -> FetchResult:
    """Full pipeline: parse sitemap → filter by age → crawl → extract → deduplicate → store.

    Parameters
    ----------
    source_configs:
        List of source config dicts with 'sitemap' (flat URL) and
        optional 'max_age_hours'.
    pool:
        ContextPool instance to store results into.
    max_articles:
        Maximum number of articles to fetch (0 = unlimited).
    max_age_hours:
        Skip entries older than this many hours (0 = no filter).
    """
    ...


def deduplicate(articles: list[Article]) -> list[Article]:
    """Remove articles with duplicate URLs, keeping first occurrence."""
    ...
```

### `ContextPool` contract (provided by separate module)

```python
@dataclass
class Article:
    publish_date: str | None
    url: str
    title: str
    body: str
    source: str
    updated_date: str | None
    category: str | None
    tags: list[str]
    author: str | None
    language: str | None


class ContextPool:
    def store_articles(self, articles: list[Article]) -> None:
        """Append articles to the day's context store as JSON Lines."""
```

### Configuration (`config.yaml`)

```yaml
sources:
  - name: svt
    sitemap: https://www.svt.se/latest-articles-sitemap.xml
    max_age_hours: 48
```

The `sitemap` URL must point to a flat `<urlset>` sitemap, not a `<sitemapindex>`.
`max_age_hours` is optional (default 0 = no filter).

### Daemon class

```python
class ContextFetcherDaemon:
    """Daemon thread that periodically fetches context at the configured fetch_time.

    Matches the start/stop/run pattern of EmailReceiver and EmailSenderQueue.
    The daemon sleeps until fetch_time, executes fetch_context(), then sleeps
    until the next day's fetch_time.
    """

    def __init__(self, pool: ContextPool, *, poll_interval: int = 3600):
        """pool: ContextPool instance to store fetched articles into.
        poll_interval: how often (seconds) the daemon checks whether it
            should fetch. Default 3600 (1 hour).
        """

    def start(self) -> None:
        """Start the daemon thread. Idempotent — safe to call multiple times."""

    def stop(self) -> None:
        """Signal the daemon thread to stop. Blocks up to 5s for join."""

    @property
    def is_running(self) -> bool:
        """True if the daemon thread is alive."""

    def _run(self) -> None:
        """Internal loop. Checks time, fetches when conditions are met, sleeps."""

    def fetch_once(self) -> FetchResult:
        """Immediate one-shot fetch, bypassing the timer. Returns fetch results.
        Useful for initial fetch at startup.
        """
```

The `_run()` method calculates seconds until the next `fetch_time` (using
`cfg.fetch_time` from config). When the time arrives, it calls `fetch_context()`
and logs the result. After fetching, it recalculates for the next day.

If `poll_interval` is short (e.g. 3600s), the daemon also falls back to the
interval-based check — waking every hour to verify the time rather than
sleeping for hours at a stretch (avoids clock-skew issues).

### Daemon state machine

```mermaid
%%{init: {
  "theme": "dark",
}}%%
stateDiagram-v2
    [*] --> Idle : created
    Idle --> Waiting : start
    Waiting --> Fetching : fetch_time
    Fetching --> Waiting : success
    Fetching --> Waiting : failed
    Waiting --> Stopped : stop
    Stopped --> [*]
```

| State | Meaning |
|---|---|
| `Idle` | Daemon exists but has not been started |
| `Waiting` | `start()` called — thread sleeping until next `fetch_time` |
| `Fetching` | `fetch_time` reached — actively fetching and storing articles |
| `Stopped` | `stop()` called — thread has exited its loop and been joined |

The two transitions from `Fetching` back to `Waiting`:

| Transition | Trigger | What happens |
|---|---|---|
| `success` | `fetch_context()` returned `FetchResult` with at least one article | Log summary: `Fetched N article(s), M warning(s)`. Articles stored in pool. Daemon sleeps until next cycle. |
| `failed` | `fetch_context()` returned `FetchResult` with zero articles, or an unexpected exception occurred | Log all errors. No articles stored. Daemon sleeps until next cycle (no crash, no retry — retries naturally on next `fetch_time`). |

Note: `fetch_context()` catches all per-URL and per-source errors internally.
An unexpected exception (the `failed` path) should be rare — the catch-all
prevents the daemon from crashing on transient infrastructure issues.

The `Stopped` state is terminal for the current thread. Calling `start()`
again creates a new thread and transitions back to `Waiting`.

## 7. Implementation Plan

### Step 1 — Scaffold

Create `daglas/context_fetcher.py` with `Article`, `FetchResult`, and stubs for `discover_sitemap_urls`, `extract_article`, `deduplicate`, `fetch_context`.

### Step 2 — Sitemap parsing

1. Fetch `sitemap_url` with `httpx`.
2. Parse XML.
3. Detect if response is a sitemap index (`<sitemapindex>`) → raise `ValueError` (config should point to a flat sitemap; use the discovery tool if unsure).
4. For flat sitemaps, iterate each `<url>` entry:
   - Extract `<loc>` (required).
   - Extract date: try `<news:publication_date>` (namespace-aware), fall back to `<lastmod>`, else `None`.
   - Extract title from `<news:title>` if present.
5. Return `list[SitemapEntry]`.

### Step 3 — Article extraction

1. For each discovered URL, fetch with `httpx` (with timeout and user-agent).
2. Pass HTML to `trafilatura.extract()` with `output_format="dict"` to get structured data.
3. Map trafilatura output keys → `Article` fields:
   - `title`, `description` → `title`
   - `raw_text` → `body`
   - `date` → `publish_date`
   - `author` → `author`
   - `categories` → `category` (first category)
   - `tags` → `tags`
   - `language` → `language`
   - `source` → derived from URL domain or config name
4. Fallback: if trafilatura returns nothing useful, try BeautifulSoup extraction of `<article>` or `<main>` content.

### Step 4 — Deduplication

- Maintain a `seen_urls: set[str]` across the run.
- Skip articles whose URL is already in the set.
- `deduplicate()` helper for testing.

### Step 5 — Pipeline integration

`fetch_context` orchestrates the stages:

```python
def fetch_context(source_configs, pool, *, max_articles=0, max_age_hours=0):
    seen: set[str] = set()
    articles: list[Article] = []
    errors: list[str] = []

    with httpx.Client(...) as client:
        for source in source_configs:
            try:
                entries = read_sitemap_entries(source["sitemap"], client)
            except Exception as e:
                errors.append(f"{source['sitemap']}: parse failed: {e}")
                continue

            # Age filter
            source_max_age = source.get("max_age_hours", 0) or max_age_hours
            if source_max_age > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=source_max_age)
                entries = [e for e in entries
                          if e.publish_date is None
                          or _parse_date(e.publish_date) >= cutoff]

            # Newest first
            entries.sort(key=_entry_sort_key, reverse=True)

            for entry in entries:
                if entry.url in seen:
                    continue
                seen.add(entry.url)
                if max_articles and len(articles) >= max_articles:
                    break
                try:
                    resp = client.get(entry.url, timeout=30)
                    resp.raise_for_status()
                    article = extract_article(entry.url, resp.text)
                    if not article.title and entry.title:
                        article.title = entry.title
                    articles.append(article)
                except Exception as e:
                    errors.append(f"{entry.url}: {e}")
            if max_articles and len(articles) >= max_articles:
                break

    articles.sort(key=lambda a: a.publish_date or "", reverse=True)
    deduped = deduplicate(articles)

    if deduped:
        pool.store_articles([a.__dict__ for a in deduped])

    return FetchResult(articles=deduped, errors=errors)
```

### Step 6 — Config integration

Add a `sources: list[dict]` field to `DaglasConfig` in `daglas/config.py`. The config module's `load_config` already reads arbitrary keys from YAML, so this just needs a dataclass field.

### Step 7 — Daemon lifecycle

Add `ContextFetcherDaemon` class to `daglas/context_fetcher.py`:

1. **`__init__`** — accept `ContextPool` instance and optional `poll_interval`. Read `cfg.fetch_time` from config. Create `_stop_event = threading.Event()`.
2. **`start()`** — clear stop event, create daemon thread targeting `_run()`, start thread.
3. **`stop()`** — set stop event, join thread with 5s timeout.
4. **`fetch_once()`** — wrap `fetch_context()` call with the configured sources from config. Return `FetchResult`.
5. **`_run()`** — loop until stop event:
   - Calculate seconds to next fetch_time (see `_resolve_send_time` pattern in `run.py`).
   - Sleep with `_stop_event.wait(timeout=seconds_to_fetch)` to allow early wake on stop.
   - When wake time arrives: call `fetch_once()` and log results.
   - After fetch: recalculate for next day (tomorrow's fetch_time).
   - If the calculated wait exceeds `poll_interval`, clamp to `poll_interval` and re-check (to handle clock changes gracefully).
6. **Config**: The `context_fetcher_poll_interval` field in `DaglasConfig` (default 3600).

## 8. Unit Test Strategy (`tests/test_context_fetcher.py`)

Use `pytest`. No network — mock `httpx` responses with fixture HTML/XML. Use `tmp_path` for any file I/O.

Coverage categories: happy path, error path, edge cases, critical business logic.

| Category | Test | What it covers |
|---|---|---|---|
| Happy path | `test_read_sitemap_entries_news_metadata` | Flat sitemap with `<news:news>` → returns `SitemapEntry` with dates and titles |
| Happy path | `test_read_sitemap_entries_plain` | Flat sitemap without news namespace → entries with dates from `<lastmod>`, titles empty |
| Happy path | `test_extract_article` | Article HTML → populated `Article` |
| Happy path | `test_fetch_context_single_sitemap` | One sitemap → articles discovered, filtered, extracted, stored |
| Error path | `test_read_sitemap_entries_index_raises` | Sitemap index → raises `ValueError` |
| Error path | `test_fetch_context_sitemap_unreachable` | Sitemap fetch fails → error recorded, no articles |
| Error path | `test_fetch_context_article_failure` | One article fails → others still processed and stored |
| Error path | `test_fetch_context_all_fail` | All sitemaps fail → no articles stored |
| Edge case | `test_read_sitemap_entries_empty` | Sitemap with no entries → empty list |
| Edge case | `test_extract_article_fallback` | No clear article → fallback extraction |
| Edge case | `test_extract_article_empty_body` | Empty/short HTML → empty-styled Article |
| Edge case | `test_fetch_context_no_sources` | Empty source list → no-op |
| Critical logic | `test_deduplicate` | Duplicate URLs → only first occurrence kept |
| Critical logic | `test_fetch_context_deduplicates_across_sitemaps` | Same URL in two sitemaps → stored once |
| Critical logic | `test_fetch_context_skips_old_articles` | Entries older than `max_age_hours` are not fetched |
| Critical logic | `test_fetch_context_sorts_by_date` | Newest articles fetched and stored first |
| Daemon | `test_daemon_start_stop` | `start()` then `stop()` → thread exits cleanly |
| Daemon | `test_daemon_fetch_once` | `fetch_once()` returns `FetchResult` with articles |
| Daemon | `test_daemon_skip_if_no_sources` | Daemon starts but no sources configured → no-op |
| Daemon | `test_daemon_is_running` | `is_running` reflects thread state |

## 9. Acceptance Criteria

- `pytest tests/test_context_fetcher.py` passes all tests.
- `ruff check daglas/` and `ruff format --check daglas/` pass.
- Running `python3 -m daglas.context_fetcher --dry-run` with a configured sitemap URL prints discovered articles (manual smoke test).
- `ContextFetcherDaemon.start()` / `stop()` lifecycle works (tested via integration script or unit tests).
- Sitemap discovery tool (separate script) can identify the correct flat sitemap URL for a new source.

## Discussion

### 2026-06-16 — Flat sitemap only; metadata-aware parsing; age filtering

**What changed:**
- Replaced `discover_sitemap_urls` with `read_sitemap_entries` — no sitemap index recursion, only flat `<urlset>` parsing. Raises `ValueError` on index input.
- Added `SitemapEntry` dataclass returning `(url, publish_date, title)` from `<news:news>` / `<lastmod>` metadata.
- Added `max_age_hours` filter to skip stale entries before HTML fetch.
- Reordered `Article` fields so `publish_date` comes first.
- Sitemap discovery separated into a future AI-assisted tool; the fetcher assumes the sitemap URL is pre-configured.
- Pipeline stages now: Parse sitemap → Filter by age → Crawl → Extract → Deduplicate → Store.

**Why:**
- The general sitemap index includes video and other non-article sitemaps.
- Without date metadata in discovery, we can't filter old entries without fetching their HTML.
- Namespace-agnostic: works with `<news:news>`, other custom namespaces, or no metadata.
- Swedish language requirement is implicit in source selection, not tag inspection.
- Manual flat-sitemap config is more reliable than auto-discovery; the discovery tool is a convenience for adding new sources.

### 2026-06-14 — Added daemon lifecycle (start/stop/run)

### 2026-06-14 — Added daemon lifecycle (start/stop/run)

**What changed:**
- Added `ContextFetcherDaemon` class with `start()`/`stop()`/`_run()` pattern, matching `EmailReceiver` and `EmailSenderQueue`.
- Removed "scheduling" from non-goals — the daemon now handles daily scheduling internally.
- Updated component diagram: no separate Scheduler; `run.py` controls the daemon via `start()`/`stop()`.
- Added `fetch_once()` for one-shot immediate fetches (e.g., initial startup fetch).
- Added `context_fetcher_poll_interval` config field (default 3600s).

**Why:**
- Consistency: all I/O modules (EmailReceiver, EmailSenderQueue, ContextFetcher) follow the same daemon pattern.
- `run.py` should only assemble and control lifecycle — each module manages its own timing.
- The daemon sleeps until `fetch_time`, runs the pipeline, then sleeps until the next day — no busy-waiting.

**Impact on implementation plan:**
- `ContextFetcher` status changes from `done` to `designing` — daemon still needs to be built.
- `run.py` needs to call `fetcher.start()` instead of calling `fetch_context()` directly (or call `fetch_once()` at startup + `start()` for ongoing scheduling).
- `DaglasConfig` needs `context_fetcher_poll_interval` field.

**TODO actions:**
- [x] Add `context_fetcher_poll_interval` field to `DaglasConfig` (default 86400).
- [x] Implement `ContextFetcherDaemon` in `daglas/context_fetcher.py`.
- [x] Add `tests/test_context_fetcher.py` tests for daemon lifecycle.
- [x] Update `run.py` — `OutboundPipeline` uses `ContextFetcherDaemon.fetch_once()` internally.
- [x] Update `implementation_plan.md`.
