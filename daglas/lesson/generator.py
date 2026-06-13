from pathlib import Path

import daglas.config


def _read_prompt(name: str) -> str:
    cfg = daglas.config.config
    prompts_dir = Path(cfg.prompts_dir) if cfg is not None else Path("prompts")
    path = prompts_dir / name
    if path.is_file():
        return path.read_text().strip()
    return ""


def _truncate_context(context: str, max_length: int) -> str:
    if len(context) <= max_length:
        return context
    return context[:max_length] + "..."


def generate_lesson(
    provider,
    context_articles: list[dict],
    *,
    dry_run: bool = False,
) -> str | None:
    cfg = daglas.config.config
    system_prompt = _read_prompt("system.md")
    user_template = _read_prompt("user.md")

    context_parts = []
    for article in context_articles:
        context_parts.append(
            f"Title: {article.get('title', '')}\n{article.get('body', '')}"
        )
    raw_context = "\n\n---\n\n".join(context_parts)
    max_len = cfg.max_context_length if cfg is not None else 500
    raw_context = _truncate_context(raw_context, max_len)

    level = cfg.lesson_level if cfg is not None else "beginner"
    vcount = cfg.vocab_count if cfg is not None else 5
    word_limit = cfg.article_word_limit if cfg is not None else 100
    user_prompt = user_template.format(
        context=raw_context,
        level=level,
        vocab_count=vcount,
        article_word_limit=word_limit,
    )

    if dry_run:
        return None

    return provider.prompt(system=system_prompt, user=user_prompt)
