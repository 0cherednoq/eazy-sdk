"""Sphinx configuration for the Eazy SDK documentation site."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sphinx.application import Sphinx

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PROJECT_METADATA = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]

project = "Eazy SDK"
author = "Eazy SDK contributors"
version = str(PROJECT_METADATA["version"])
release = version
language = "ru"

extensions = [
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_llms_txt",
    "sphinxcontrib.mermaid",
]

source_suffix = {
    ".md": "markdown",
    ".mdx": "markdown",
}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
myst_heading_anchors = 3
myst_links_external_new_tab = True
myst_title_to_header = True

html_theme = "shibuya"
html_title = f"{project} {release}"
html_favicon = "src/content/docs/_static/favicon.svg"
html_theme_options = {
    "accent_color": "green",
    "globaltoc_expand_depth": 1,
    "nav_links": [
        {"title": "Руководство", "url": "guide/index"},
        {"title": "Интеграции", "url": "integrations/http/index"},
        {"title": "Архитектура", "url": "architecture/index"},
        {"title": "API", "url": "reference/api/index"},
    ],
}
html_static_path = ["src/content/docs/_static"]
html_css_files = ["css/custom.css"]
html_show_sourcelink = False
# Сайт живёт в подкаталоге GitHub Pages; адрес нужен canonical-ссылкам и llms.txt.
html_baseurl = "https://0cherednoq.github.io/eazy-sdk/"

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

llms_txt_title = project
llms_txt_summary = str(PROJECT_METADATA["description"])
llms_txt_uri_template = "{base_url}{docname}/"

nitpicky = True


def _fix_llms_index_links(app: Sphinx, exception: Exception | None) -> None:
    """Сократить `start/index/` до `start/`: так dirhtml отдаёт индексные страницы."""
    if exception is not None:
        return
    llms = Path(app.outdir) / "llms.txt"
    if llms.exists():
        text = llms.read_text(encoding="utf-8")
        llms.write_text(text.replace("/index/)", "/)"), encoding="utf-8", newline="\n")


def setup(app: Sphinx) -> None:
    # Приоритет выше 500: расширение sphinx_llms_txt пишет файл в том же событии раньше.
    app.connect("build-finished", _fix_llms_index_links, priority=900)
