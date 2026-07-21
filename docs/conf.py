"""Sphinx configuration."""

project = "edutap.pass_builder"
author = "eduTAP"

extensions = ["myst_parser"]

myst_enable_extensions = ["colon_fence", "deflist"]

html_theme = "alabaster"
exclude_patterns = ["_build", "superpowers/**"]
