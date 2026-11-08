CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Wshadow -Wconversion -Wno-sign-conversion

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  EXT = dylib
  SHARED = -dynamiclib
else
  EXT = so
  SHARED = -shared
endif

all: dateutil_parse.$(EXT)

dateutil_parse.$(EXT): dateutil_parse.c dateutil_unicode.h
	$(CC) $(CFLAGS) -fPIC $(SHARED) $< -o $@

# Parser core with AddressSanitizer + UndefinedBehaviorSanitizer
build/du_cli_asan: tools/du_cli.c dateutil_parse.c dateutil_unicode.h
	mkdir -p build
	$(CC) -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all \
	  -fno-omit-frame-pointer $< -o $@

test: dateutil_parse.$(EXT)
	python3 -m pytest tests -v

clean:
	rm -rf dateutil_parse.so dateutil_parse.dylib build

.PHONY: all test clean
