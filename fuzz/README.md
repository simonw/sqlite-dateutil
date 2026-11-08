# Fuzzing

`fuzz_parse.c` is a [libFuzzer](https://llvm.org/docs/LibFuzzer.html) harness for the parser
core, built with AddressSanitizer and UndefinedBehaviorSanitizer. The first 7 bytes of each
input pick the flags and the "today" date that fills in missing fields. The rest is the text
to parse. Besides crashes and sanitizer errors, it checks that every result is well-formed
ISO 8601, that parsing is deterministic, and that the result parses back to itself.

CI (`.github/workflows/fuzz.yml`) fuzzes for 3 minutes on every push and pull request, and
for 30 minutes daily if `main` has had a commit in the last 24 hours. It keeps a minimized corpus in the Actions cache and uploads any
crashing input as the `fuzz-crashes` artifact.

## Running it locally

You need a clang that includes libFuzzer: clang on Linux, or `brew install llvm` on macOS
(Apple's clang doesn't include it).

```bash
uv run python tools/build.py --fuzz
uv run python fuzz/make_seeds.py build/fuzz/seeds
mkdir -p build/fuzz/corpus
build/fuzz_parse -dict=fuzz/dateutil.dict -max_len=4096 -timeout=2 \
  build/fuzz/corpus build/fuzz/seeds
```

Or, in Docker:

```bash
docker run --rm -it -v "$PWD:/src:ro" ghcr.io/astral-sh/uv:debian sh -c \
  'cp -r /src /work && cd /work && rm -rf .venv build && apt-get update -qq && apt-get install -y -qq clang && uv run python tools/build.py --fuzz && uv run python fuzz/make_seeds.py build/fuzz/seeds && mkdir -p build/fuzz/corpus && build/fuzz_parse -max_total_time=300 -dict=fuzz/dateutil.dict -max_len=4096 build/fuzz/corpus build/fuzz/seeds'
```

## Reproducing a crash

Download the `fuzz-crashes` artifact, then replay the file with any compiler that supports
ASan, including Apple clang:

```bash
uv run python tools/build.py --fuzz-replay
build/fuzz_replay crash-<hash>
```

## Files

```
fuzz_parse.c    libFuzzer harness and oracles
replay.c        runs the harness on files, without libFuzzer
dateutil.dict   dateutil keywords, separators and number shapes
make_seeds.py   seed corpus from the test suite's hand-picked inputs
```
