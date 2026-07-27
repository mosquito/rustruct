SPHINXOPTS ?= -W --keep-going
SPHINXBUILD ?= uv run --group docs sphinx-build
SOURCEDIR = .
BUILDDIR = _build

.PHONY: help html test-docs

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)

html:
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS)

test-docs: html
	@uv run pytest ../README.md . -q
