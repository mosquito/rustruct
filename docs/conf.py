"""Sphinx configuration for the rustruct documentation."""

from importlib.metadata import version

project = "rustruct"
author = "Dmitry Orlov"
copyright = "2026, Dmitry Orlov"
release = version("rustruct")
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = {"colon_fence", "deflist", "fieldlist"}
myst_heading_anchors = 3

autodoc_member_order = "bysource"
autodoc_preserve_defaults = True
autodoc_typehints = "signature"
autodoc_typehints_format = "short"

html_theme = "furo"
html_title = project
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#087f5b",
        "color-brand-content": "#087f5b",
    },
    "dark_css_variables": {
        "color-brand-primary": "#63e6be",
        "color-brand-content": "#63e6be",
    },
}
html_show_sourcelink = False
