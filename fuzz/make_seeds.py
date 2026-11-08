"""
Write a libFuzzer seed corpus from the test suite's hand-picked inputs.

    uv run python fuzz/make_seeds.py build/fuzz/seeds

Each seed is the 7-byte header fuzz_parse.c expects (flags + default date
+ current year) followed by a test input, so the fuzzer starts from inputs
that already reach deep into the parser.
"""
import hashlib
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "tools")]

from test_dateutil_parse import CASES  # noqa: E402

# (year, month, day, cur_year) defaults that exercise edge cases
DEFAULTS = [(2024, 1, 31, 2024), (2023, 3, 30, 2023), (2000, 2, 29, 2000),
            (9999, 12, 28, 9999), (1, 1, 1, 1)]


def header(flags, year, month, day, cur_year):
    # fuzz_parse.c computes 1 + (value % 9999), 1 + (byte % 12), etc.
    return struct.pack("<BHBBH", flags, year - 1, month - 1, day - 1, cur_year - 1)


def main(out_dir):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for i, text in enumerate(CASES):
        flags = i % 4
        y, m, d, cy = DEFAULTS[i % len(DEFAULTS)]
        data = header(flags, y, m, d, cy) + text.encode("utf-8")
        (out / hashlib.sha1(data).hexdigest()).write_bytes(data)
        count += 1
    print(f"wrote {count} seeds to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "fuzz" / "seeds")
