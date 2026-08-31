"""Sphinx configuration for the Eazy SDK documentation site."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PROJECT_METADATA = tomllib.loads(
    (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]

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
html_theme_options = {
    "accent_color": "green",
    "globaltoc_expand_depth": 1,
}
html_static_path = ["src/content/docs/_static"]
html_css_files = ["css/custom.css"]
html_show_sourcelink = False

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

llms_txt_title = project
llms_txt_summary = str(PROJECT_METADATA["description"])
llms_txt_uri_template = "{base_url}{docname}/"

nitpicky = True
