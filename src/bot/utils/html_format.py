"""HTML formatting utilities for Telegram messages.

Telegram's HTML mode only requires escaping 3 characters (<, >, &) vs the many
ambiguous Markdown v1 metacharacters, making it far more robust for rendering
Claude's output which contains underscores, asterisks, brackets, etc.
"""

import re
from typing import List, Tuple


def _strip_inline_markdown(text: str) -> str:
    """Remove inline markdown markers from text destined for a <pre> block.

    Telegram renders nothing but monospace inside <pre>, so leftover
    backticks/asterisks/underscores would show up literally. Since the block
    is already monospace, we just drop the markers and keep the content.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold
    text = re.sub(r"`([^`]+)`", r"\1", text)  # inline code
    text = re.sub(r"~~(.+?)~~", r"\1", text)  # strikethrough
    return text


def escape_html(text: str) -> str:
    """Escape the 3 HTML-special characters for Telegram.

    This replaces all 3 _escape_markdown functions previously scattered
    across the codebase.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_markdown_tables(text: str) -> str:
    """Convert markdown pipe-tables to <pre>-wrapped monospace tables."""
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1

            # Filter separator rows like |---|---|
            data_rows = [
                l for l in table_lines
                if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", l)
            ]

            if data_rows:
                rows = []
                for row in data_rows:
                    cells = [
                        escape_html(
                            _strip_inline_markdown(re.sub(r"<[^>]+>", "", c).strip())
                        )
                        for c in row.strip().strip("|").split("|")
                    ]
                    rows.append(cells)

                col_count = max(len(r) for r in rows)
                col_widths = [0] * col_count
                for row in rows:
                    for j, cell in enumerate(row):
                        if j < col_count:
                            col_widths[j] = max(col_widths[j], len(cell))

                formatted = []
                for idx, row in enumerate(rows):
                    padded = [
                        (row[j] if j < len(row) else "").ljust(col_widths[j])
                        for j in range(col_count)
                    ]
                    formatted.append("  ".join(padded).rstrip())
                    if idx == 0:
                        formatted.append("  ".join("─" * w for w in col_widths))

                result.append("<pre>" + "\n".join(formatted) + "</pre>")
            continue

        result.append(lines[i])
        i += 1

    return "\n".join(result)


def markdown_to_telegram_html(text: str) -> str:
    """Convert Claude's markdown output to Telegram-compatible HTML.

    Telegram supports a narrow HTML subset: <b>, <i>, <code>, <pre>,
    <a href>, <s>, <u>. This function converts common markdown patterns
    to that subset while preserving code blocks verbatim.

    Order of operations:
    0. Convert markdown tables to <pre> blocks
    0b. Convert horizontal rules (---) to a separator line
    0c. Extract existing Telegram HTML tags -> placeholders
    1. Extract fenced code blocks -> placeholders
    2. Extract inline code -> placeholders
    3. HTML-escape remaining text
    4. Convert bold (**text** / __text__)
    5. Convert italic (*text*, _text_ with word boundaries)
    6. Convert links [text](url)
    7. Convert headers (# Header -> <b>Header</b>)
    8. Convert strikethrough (~~text~~)
    9. Restore placeholders
    """
    placeholders: List[Tuple[str, str]] = []
    placeholder_counter = 0

    def _make_placeholder(html_content: str) -> str:
        nonlocal placeholder_counter
        key = f"\x00PH{placeholder_counter}\x00"
        placeholder_counter += 1
        placeholders.append((key, html_content))
        return key

    # --- 0. Convert markdown tables to <pre> blocks ---
    text = _convert_markdown_tables(text)

    # --- 0b. Convert horizontal rules (--- on its own line) ---
    text = re.sub(r"^\s*-{3,}\s*$", "─" * 20, text, flags=re.MULTILINE)

    # --- 0c. Extract existing Telegram-compatible HTML tags as placeholders ---
    # Handles Claude output that already contains HTML markup so escape_html
    # in step 3 doesn't destroy the tags.
    def _extract_html(m: re.Match) -> str:  # type: ignore[type-arg]
        return _make_placeholder(m.group(0))

    # Multi-line <pre> blocks first (greedy content, so longest match wins)
    text = re.sub(r"<pre(?:[^>]*)>.*?</pre>", _extract_html, text, flags=re.DOTALL)
    # Inline formatted tags
    for tag in ("b", "strong", "i", "em", "u", "s", "strike", "del", "code"):
        text = re.sub(rf"<{tag}>.*?</{tag}>", _extract_html, text, flags=re.DOTALL)
    # Anchor tags
    text = re.sub(r'<a\s+href="[^"]*">.*?</a>', _extract_html, text, flags=re.DOTALL)

    # --- 1. Extract fenced code blocks ---
    def _replace_fenced(m: re.Match) -> str:  # type: ignore[type-arg]
        lang = m.group(1) or ""
        code = m.group(2)
        escaped_code = escape_html(code)
        if lang:
            html = f'<pre><code class="language-{escape_html(lang)}">{escaped_code}</code></pre>'
        else:
            html = f"<pre><code>{escaped_code}</code></pre>"
        return _make_placeholder(html)

    text = re.sub(
        r"```(\w+)?\n(.*?)```",
        _replace_fenced,
        text,
        flags=re.DOTALL,
    )

    # --- 2. Extract inline code ---
    def _replace_inline_code(m: re.Match) -> str:  # type: ignore[type-arg]
        code = m.group(1)
        escaped_code = escape_html(code)
        return _make_placeholder(f"<code>{escaped_code}</code>")

    text = re.sub(r"`([^`\n]+)`", _replace_inline_code, text)

    # --- 3. HTML-escape remaining text ---
    text = escape_html(text)

    # --- 4. Bold: **text** or __text__ ---
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # --- 5. Italic: *text* (require non-space after/before) ---
    text = re.sub(r"\*(\S.*?\S|\S)\*", r"<i>\1</i>", text)
    # _text_ only at word boundaries (avoid my_var_name)
    text = re.sub(r"(?<!\w)_(\S.*?\S|\S)_(?!\w)", r"<i>\1</i>", text)

    # --- 6. Links: [text](url) ---
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )

    # --- 7. Headers: # Header -> <b>Header</b> ---
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # --- 8. Strikethrough: ~~text~~ ---
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # --- 9. Restore placeholders ---
    for key, html_content in placeholders:
        text = text.replace(key, html_content)

    return text
