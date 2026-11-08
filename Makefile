# Convenience wrapper. The real build logic lives in tools/build.py (which
# `uv run pytest` calls automatically); this Makefile just delegates to it
# so there is one definition that works on macOS, Linux and Windows.
UV ?= uv

all:
	$(UV) run python tools/build.py

asan:
	$(UV) run python tools/build.py --asan

test:
	$(UV) run pytest

clean:
	rm -rf dateutil_parse.so dateutil_parse.dylib dateutil_parse.dll build

.PHONY: all asan test clean
