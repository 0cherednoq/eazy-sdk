"""Tests for prose extraction used by the humanizer-ru wrapper."""

from scripts.lint_docs_prose import extract_prose


def test_extract_prose_removes_page_scaffolding_and_preserves_lines() -> None:
    source = """---
title: Test
---

# Заголовок

Текст с `ApiName` и [внешней ссылкой](https://example.org).

```python
assert ApiName()
```

::::{tab-set}
:::{tab-item} HTTP
Скрытая директива.
:::
::::

Финальный абзац.
"""

    prose = extract_prose(source)

    assert len(prose.splitlines()) == len(source.splitlines())
    assert "title: Test" not in prose
    assert "assert ApiName" not in prose
    assert "Скрытая директива" not in prose
    assert "ApiName" not in prose
    assert "Текст с  и внешней ссылкой." in prose
    assert "Финальный абзац." in prose
