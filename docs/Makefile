SOURCEDIR     = .
BUILDDIR      = _build

# Put it first so that "make" without argument is like "make help".
help:
	sphinx-build -M help "$(SOURCEDIR)" "$(BUILDDIR)"

.PHONY: help Makefile

# Catch-all target: route all unknown targets to Sphinx using the make_mode module.
%: Makefile
	sphinx-build -M $@ "$(SOURCEDIR)" "$(BUILDDIR)"
