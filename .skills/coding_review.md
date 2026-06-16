# Coding Review Standards

## Naming

- Use clear, unabbreviated names. `default_path`, not `dflt_path`; `config`, not `cfg`.
- Abbreviations are only acceptable for widely-known standards (e.g., `yaml`, `smtp`, `llm`, `rss`).
- Boolean parameters should read as yes/no questions: `create_if_missing`, not `create`.

## Python Style

- Format with `ruff format`; lint with `ruff check` before every commit.
- Import standard library first, then third-party, then local (one blank line between groups).
- Use `pathlib` for filesystem paths, never `os.path`.
- Type hints required for all function signatures. Use `| None` union syntax (Python 3.10+).

## Architecture

- Each module has one clear responsibility. Files should be independently replaceable.
- No cloud dependencies — prefer local-first solutions (llama.cpp, ollama, mlx).
- No secrets in code. Configuration lives in `config.yaml` or environment variables.
- No hardcoded file paths — use `pathlib` relative to module location.

## Testing

- Tests live in a top-level `tests/` directory, mirrored to `daglas/` structure:
  ```
  daglas/
      crawler.py
      parser.py
  tests/
      test_crawler.py
      test_parser.py
  ```
- Use `pytest`. Tests must be isolated — no network, no real file I/O (use `tmp_path`).
- Cover: happy path, error path, edge cases, and critical business logic.

## Dependencies

Prefer the Python standard library. Every new third-party dependency adds maintenance burden, security surface, and build complexity.

- **No dependency is added without justification** in the task doc or commit message.
- Justification must explain what stdlib cannot do and why the library is worth the cost.
- Prefer libraries that are already available in the runtime environment (check with `python3 -c "import ..."`) before adding a new `requirements.txt` entry.
- Pin major version at minimum (e.g., `httpx>=0.28`), not a full semver lock, unless the project has a lockfile.

## Complexity limits

AI-generated code tends to produce deep nesting and long functions. Keep it readable:

- **Functions** should generally stay under 50 lines.
- **Nesting** should not exceed 3 levels of indentation (beyond the function body).
- **Loops** — prefer `for` over `while`; if `while` is needed, ensure the exit condition is obvious.
- **Early return** — validate inputs and return early instead of wrapping the entire body in `if`.

These are guidelines, not absolute rules. If a function naturally needs more, split it into smaller named helpers.

## Error handling

All external operations must handle failures explicitly. AI-generated code commonly omits this.

| Domain | Requirements |
|---|---|
| Network | Timeout, connection reset, DNS failure, non-2xx status — catch and log, never crash the pipeline |
| Filesystem | Missing file, permission denied, disk full — fall back gracefully, report error |
| LLM inference | Timeout, empty response, malformed output — retry once, then fail with a clear message |
| Subprocesses | Non-zero exit, stderr output — capture and log, do not silently ignore |
| Configuration | Missing required keys, invalid types, bad YAML — fail early with a descriptive error |

Wrap external calls in try/except at the appropriate boundary (not so broad it swallows bugs, not so narrow every line is wrapped). Log the failure context (what failed, why) before recovering or propagating.

## Documentation

- Module-level task specs live in `tasks/<module_name>.md`.
- Changes to design must be reflected in the task doc before or alongside code changes.
