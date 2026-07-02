from __future__ import annotations

from pathlib import Path

import daglas.config


def _read_prompt(name: str) -> str:
    cfg = daglas.config.config
    prompts_dir = Path(cfg.prompts_dir) if cfg is not None else Path("prompts")
    path = prompts_dir / name
    if path.is_file():
        return path.read_text().strip()
    return ""


def generate_lesson(
    provider,
    context_articles: list[dict],
    *,
    level: str | None = None,
    vocab_count: int | None = None,
    dry_run: bool = False,
) -> str | None:
    cfg = daglas.config.config
    system_template = _read_prompt("system.md")
    user_template = _read_prompt("user.md")

    context_parts = []
    for article in context_articles:
        context_parts.append(
            f"Title: {article.get('title', '')}\n{article.get('body', '')}"
        )
    raw_context = "\n\n---\n\n".join(context_parts)

    effective_level = level if level else (cfg.lesson_level if cfg else "beginner")
    effective_vcount = vocab_count if vocab_count else (cfg.vocab_count if cfg else 5)
    word_limit = cfg.article_word_limit if cfg is not None else 100
    system_prompt = system_template.format(vocab_count=effective_vcount)
    user_prompt = user_template.format(
        context=raw_context,
        level=effective_level,
        vocab_count=effective_vcount,
        article_word_limit=word_limit,
    )

    if dry_run:
        return None

    lesson_text = provider.prompt(system=system_prompt, user=user_prompt)
    return _validate_lesson(lesson_text)


_VALIDATION_ERROR_PREFIXES = ("[", "Error", "Traceback", "Exception")


def _validate_lesson(text: str) -> str | None:
    if not text or not text.strip():
        return None
    stripped = text.strip()
    if stripped.startswith(_VALIDATION_ERROR_PREFIXES):
        return None
    return text
