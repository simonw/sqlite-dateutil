"""
Differential tests: the SQLite functions must return exactly what
dateutil.parser.parse(...).isoformat() returns (or NULL where dateutil
raises), for hand-picked inputs and for tens of thousands of generated ones.
"""
import datetime
import pathlib
import random
import struct
import subprocess
import time
import zlib

import pytest

import build
from conftest import FLAGS, ROOT, VARIANTS, ref

# --------------------------------------------------------------------------
# Hand-picked inputs, largely drawn from the formats dateutil documents and
# tests. Several deliberately fail in dateutil, and must give NULL here.

CASES = [
    # ISO 8601 and variants
    "2003-09-25T10:49:41",
    "2003-09-25T10:49:41.5",
    "2003-09-25T10:49:41.502",
    "2003-09-25T10:49:41,502",
    "2003-09-25T10:49:41.1234567",
    "2003-09-25T10:49",
    "2003-09-25T10",
    "2003-09-25",
    "2003-09",
    "2003",
    "20030925T104941",
    "20030925T1049",
    "20030925T10",
    "20030925",
    "200309251049",
    "20030925104941",
    "19990101T235959.5",
    "030925",
    "2003-09-25T10:49:41Z",
    "2003-09-25T10:49:41z",
    "2003-09-25T10:49:41+00:00",
    "2003-09-25T10:49:41-03:00",
    "2003-09-25T10:49:41+0530",
    "2003-09-25T10:49:41 -03",
    "2003-09-25 10:49:41 +3",
    "2003-09-25T10:49:41-00:30",
    "2003-09-25T10:49:41+24:00",
    "2003-09-25T10:49:41+99:99",
    "2003-09-25T10:49:41.5-03:00",
    # Unix date / RFC 2822 style
    "Thu Sep 25 10:36:28 BRST 2003",
    "Thu Sep 25 10:36:28 2003",
    "Thu Sep 25 10:36:28",
    "Thu Sep 10:36:28",
    "Thu 10:36:28",
    "Sep 10:36:28",
    "10:36:28",
    "10:36",
    "Sep 2003",
    "Sep",
    "2003",
    "Thu, 25 Sep 2003 10:49:41 -0300",
    "Thu, 25 Sep 2003 10:49:41 -0300 (BRST)",
    "Wed, 1 Jan 2020 00:00:00 GMT",
    "Wed, 01 Jan 2020 00:00:00 UTC",
    "10:00 GMT+3",
    "10:00 GMT-3",
    "10:00 BRST+3",
    "10:00 UTC+05:30",
    "10:00 EST",
    "10:00 ABCDEF",
    # Month names, separators
    "Sep 25 2003",
    "September 25 2003",
    "Sept 25 2003",
    "25 Sep 2003",
    "Sep-25-2003",
    "Sep/25/2003",
    "Sep.25.2003",
    "25-Sep-2003",
    "2003-Sep-25",
    "25 September, 2003",
    "September of 2003",
    "September of 03",
    "Jan of 01",
    "Feb 30",
    "Feb 29 2023",
    "Feb 29 2024",
    "29 Feb",
    "Apr 31",
    "December 31 9999",
    "January 1 0001",
    "January 1 0000",
    "January 1 10000",
    # Numeric dates and ambiguity
    "10-09-2003",
    "10.09.2003",
    "10/09/2003",
    "10/09/03",
    "13/09/03",
    "09/13/03",
    "99/01/01",
    "01/99/01",
    "01/01/99",
    "1/2/3",
    "3/2/1",
    "32/1/1",
    "10 09 2003",
    "10 09 03",
    "2003 09 25",
    "1 2",
    "13 1",
    "1 13",
    "1 1 1 1",
    "12 99",
    "99 12",
    "001",
    "0099-01-01",
    # Times with labels, am/pm
    "10h36m28.5s",
    "10h36m28s",
    "10h36m",
    "10h36",
    "10 h 36",
    "10 h",
    "1.5h",
    "1.7h",
    "1.75 hours",
    "36.5m",
    "28.5 seconds",
    "5 minutes",
    "10am",
    "10 am",
    "10pm",
    "10 pm",
    "12am",
    "12pm",
    "12 a.m.",
    "12 p.m.",
    "10:30 am",
    "10:30 PM",
    "10:30:15 p",
    "13 pm",
    "13:00 pm",
    "0 am",
    "am",
    "10 am pm",
    "Today is January 1, 2047 at 8:21:00AM",
    "January 1, 2047 at 8:21:00AM",
    "Jan 1st, 2047",
    "Jan 2nd 2047",
    "the 3rd of May 2001",
    "May 3rd 2001 at 5pm",
    # Weekdays (relative to today when there is no day)
    "Monday",
    "Mon",
    "Tuesday 10am",
    "Sunday Sep 2003",
    "Friday 13",
    # Invalid
    "",
    " ",
    "  , ",
    "junk",
    "10:61",
    "25:00",
    "24:00",
    "10:30:60",
    "13/13/2003",
    "2003-13-01",
    "2003-00-01",
    "2003-01-32",
    "0",
    "00",
    "nan",
    "inf",
    "infinity",
    "10 nan",
    "1.e",
    "10:",
    ":10",
    "10:a",
    "10::30",
    "-",
    "+",
    "10 +",
    "10:00 +",
    "10:00 +123",
    "10:00 + 03:",
    "Jan-",
    "Jan-01-",
    "01-",
    "01-01-",
    "2003-09-25T10:49:41.",
    "123456789012345678901234567890",
    "99999999999999999999:00",
    "1.12345678901234567890123h",
    # Fuzzy-only successes
    "I have a meeting on March 1, 1974",
    "On June 8th, 2020, I am going to be the first man on Mars",
    "Meet me at the AM/PM on Sunset at 3:00 AM on December 3rd, 2003",
    "Meet me at 3:00AM on December 3rd, 2003 at the AM/PM on Sunset",
    "Jan 29, 1945 14:45 AM I going to see you there?",
    "2017-07-17 06:15:",
    "the timestamp was 1:30pm UTC yesterday",
    "invoice #12345 dated 2021-03-04",
    "I ate 3 apples at 10am",
    # Control characters, NUL, whitespace variants
    "2003-09-25\x0010:49",
    "2003\x00-09-25",
    "2003-09-25\t10:49",
    "2003-09-25\n10:49",
    "2003-09-25\x1c10:49",
    "Sep\xa025\xa02003",
    "Sep 25　2003",
    "Sep 25 2003\x01",
    # Non-ASCII letters (treated as letters by both)
    "25 Sep 2003 é",
    "été 2003",
]


@pytest.mark.parametrize("fn", list(VARIANTS))
@pytest.mark.parametrize("text", CASES)
def test_cases_match_dateutil(sql, fn, text):
    assert sql(fn, text) == ref(text, **VARIANTS[fn])


# --------------------------------------------------------------------------
# A few explicit expectations, as readable documentation of behavior.

@pytest.mark.parametrize(
    "fn,text,expected",
    [
        ("dateutil_parse", "2003-09-25T10:49:41.5-03:00", "2003-09-25T10:49:41.500000-03:00"),
        ("dateutil_parse", "Thu Sep 25 10:36:28 BRST 2003", "2003-09-25T10:36:28"),
        ("dateutil_parse", "Wed, 01 Jan 2020 00:00:00 GMT", "2020-01-01T00:00:00+00:00"),
        ("dateutil_parse", "10/09/2003", "2003-10-09T00:00:00"),
        ("dateutil_parse_dayfirst", "10/09/2003", "2003-09-10T00:00:00"),
        ("dateutil_parse", "19990101T235959.5", "1999-01-01T23:59:59.500000"),
        ("dateutil_parse", "Today is January 1, 2047 at 8:21:00AM", None),
        ("dateutil_parse_fuzzy", "Today is January 1, 2047 at 8:21:00AM", "2047-01-01T08:21:00"),
        ("dateutil_parse", "not a date", None),
        ("dateutil_parse", "2003-02-30", None),
    ],
)
def test_explicit(sql, fn, text, expected):
    assert sql(fn, text) == expected


# --------------------------------------------------------------------------
# Generated inputs

TOKENS = (
    # numbers of every length the parser special-cases
    ["0", "00", "1", "01", "4", "7", "9", "10", "12", "13", "23", "24", "25",
     "29", "30", "31", "32", "59", "60", "99", "100", "101", "007", "1999",
     "2003", "0000", "10000", "030925", "235959", "20030925",
     "200309251049", "20030925104941", "19990101", "123456789012345678901",
     "1.5", "10.5", "0.75", "1.25", "59.999999", "235959.5", "10,5", "2.",
     "1.2.3", ".5"]
    # names
    + ["Jan", "January", "feb", "MAR", "Apr", "May", "Jun", "jul", "Aug",
       "Sep", "Sept", "September", "Oct", "Nov", "Dec", "December",
       "Mon", "Monday", "tue", "Wed", "Thursday", "Fri", "Sat", "Sun"]
    + ["am", "pm", "AM", "PM", "a", "p", "a.m.", "p.m."]
    + ["h", "m", "s", "hour", "hours", "minute", "minutes", "second", "seconds"]
    + ["of", "at", "on", "and", "ad", "st", "nd", "rd", "th", "T", "t"]
    + ["Z", "z", "UTC", "GMT", "utc", "gmt", "EST", "BRST", "CEST", "ABCDE",
       "ABCDEF", "X"]
    + ["nan", "inf", "foo", "x", "é", "日本", "e\u0301", "\u2013", "\u2019",
       "\u00b2", "\u00b7", "\u20ac", "\U0001F600", "\u3001", "\uff0f"]
    # punctuation and whitespace
    + [":", ":", "-", "-", "/", ".", ",", ";", "'", "+", "+", "(", ")", "#",
       " ", " ", " ", "  ", "\t", "\n", "\x00", "\xa0"]
)


def gen_tokens(rng):
    parts = []
    for _ in range(rng.randint(1, 12)):
        r = rng.random()
        if r < 0.15:
            parts.append(str(rng.randint(0, 10 ** rng.randint(1, 8))))
        elif r < 0.2:
            parts.append(f"{rng.randint(0, 99)}.{rng.randint(0, 10**6)}")
        else:
            parts.append(rng.choice(TOKENS))
    return "".join(parts)


def gen_structured(rng):
    """Datetime-shaped strings with randomly perturbed components."""
    y = rng.choice([rng.randint(1, 9999), rng.randint(0, 99), 0, 10000])
    mo = rng.randint(0, 14)
    d = rng.randint(0, 33)
    H, M, S = rng.randint(0, 25), rng.randint(0, 61), rng.randint(0, 61)
    frac = rng.choice(["", f".{rng.randint(0, 10**rng.randint(1, 9))}"])
    tz = rng.choice(["", "Z", "z", " UTC", " GMT", " EST", "+00:00",
                     f"{rng.choice('+-')}{rng.randint(0, 25):02}:{rng.randint(0, 99):02}",
                     f"{rng.choice('+-')}{rng.randint(0, 2500):04}",
                     f" GMT{rng.choice('+-')}{rng.randint(0, 14)}",
                     f" {rng.choice('+-')}0300 (BRST)"])
    month_name = rng.choice(["Jan", "February", "Sep", "Sept", "dec", "May"])
    ampm = rng.choice(["am", "pm", " AM", " p.m."])
    fmt = rng.choice([
        f"{y:04}-{mo:02}-{d:02}T{H:02}:{M:02}:{S:02}{frac}{tz}",
        f"{y:04}-{mo:02}-{d:02} {H}:{M:02}{tz}",
        f"{y:04}{mo:02}{d:02}T{H:02}{M:02}{S:02}{frac}",
        f"{y:04}{mo:02}{d:02}",
        f"{mo}/{d}/{y}",
        f"{d}.{mo}.{y}",
        f"{d}-{mo}-{y:02}",
        f"{d} {month_name} {y} {H}:{M:02}{ampm}",
        f"{month_name} {d}, {y} at {H % 13}{ampm}",
        f"{month_name}-{d}-{y}",
        f"{y} {month_name} {d}",
        f"{H}h{M}m{S}{frac}s",
        f"{H}:{M}{frac}",
        f"{d} of {month_name}",
        f"{month_name} of {y}",
        f"Meeting on {month_name} {d} {y} at {H}:{M:02}{tz}, room 101",
    ])
    return fmt


def corpus(seed, n):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(gen_tokens(rng) if rng.random() < 0.5 else gen_structured(rng))
    return out


@pytest.mark.parametrize("fn", list(VARIANTS))
def test_generated_match_dateutil(sql, fn):
    kwargs = VARIANTS[fn]
    mismatches = []
    for text in corpus(seed=zlib.crc32(fn.encode()), n=10_000):
        got, want = sql(fn, text), ref(text, **kwargs)
        if got != want:
            mismatches.append((text, got, want))
    assert mismatches == [], mismatches[:20]


# --------------------------------------------------------------------------
# The parser core under AddressSanitizer + UBSan, with fixed default dates.
# This exercises the "default day is past the end of the month" logic and
# weekday arithmetic deterministically, and checks memory safety on
# arbitrary bytes.

@pytest.fixture(scope="session")
def asan_cli():
    try:
        return build.build_asan()
    except build.BuildError as e:
        pytest.skip("could not build sanitizer harness: " + str(e)[-500:])


def run_cli(cli, default, cur_year, records):
    payload = b"".join(
        struct.pack("<BI", flags, len(data)) + data for flags, data in records
    )
    proc = subprocess.run(
        [str(cli), str(default.year), str(default.month), str(default.day), str(cur_year)],
        input=payload, capture_output=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode()[-3000:]
    assert b"runtime error" not in proc.stderr, proc.stderr.decode()[-3000:]
    lines = proc.stdout.decode().split("\n")[:-1]
    assert len(lines) == len(records)
    return [None if line == "NULL" else line for line in lines]


@pytest.mark.parametrize("default", [
    datetime.datetime(2024, 1, 31),    # day 31 clamps in short months
    datetime.datetime(2023, 3, 30),    # Feb clamping, non-leap
    datetime.datetime(2000, 2, 29),    # leap day default
    datetime.datetime(9999, 12, 28),   # weekday jump can overflow
    datetime.datetime(1, 1, 1),
])
def test_sanitized_core_matches_dateutil(asan_cli, default):
    cur_year = time.localtime().tm_year   # what dateutil uses for 2-digit years
    texts = CASES + corpus(seed=default.toordinal(), n=4000)
    records, expected = [], []
    for text in texts:
        for kwargs in VARIANTS.values():
            flags = sum(FLAGS[k] for k in kwargs)
            records.append((flags, text.encode("utf-8")))
            expected.append(ref(text, default=default, **kwargs))
    got = run_cli(asan_cli, default, cur_year, records)
    mismatches = [(r[1], g, e) for r, g, e in zip(records, got, expected) if g != e]
    assert mismatches == [], mismatches[:20]


def test_sanitized_core_random_bytes(asan_cli):
    """Arbitrary bytes, including invalid UTF-8, must never crash."""
    rng = random.Random(1234)
    alphabet = b"0123456789:-/.,+() \t\x00TZamph" + bytes([0xC3, 0xA9, 0xE2, 0x80, 0xFF, 0xF4])
    records = []
    for _ in range(20_000):
        n = rng.randint(0, 40)
        if rng.random() < 0.5:
            data = bytes(rng.choice(alphabet) for _ in range(n))
        else:
            data = bytes(rng.randrange(256) for _ in range(n))
        records.append((rng.randrange(4), data))
    # Big inputs: heap path, and over the size limit
    records.append((1, b"9" * 300))
    records.append((1, b"Jan " * 16000))
    records.append((1, b"1." * 32000))
    records.append((0, b"x" * 70000))
    run_cli(asan_cli, datetime.datetime(2020, 6, 15), 2020, records)


# --------------------------------------------------------------------------
# SQL-level behavior and safety

def test_null_and_non_text(sql):
    assert sql("dateutil_parse", None) is None
    assert sql("dateutil_parse", 20030925) == "2003-09-25T00:00:00"
    assert sql("dateutil_parse", 2003.5) == ref("2003.5")
    assert sql("dateutil_parse", b"2003-09-25") == "2003-09-25T00:00:00"
    assert sql("dateutil_parse", b"\xff\xfe\x00junk") is None


def test_defaults_to_today(sql):
    today = datetime.date.today()
    assert sql("dateutil_parse", "10:30") == f"{today.isoformat()}T10:30:00"
    assert sql("dateutil_parse", "10:30") == ref("10:30")


def test_two_digit_years_follow_dateutil(sql):
    for yy in range(100):
        text = f"01/01/{yy:02}"
        assert sql("dateutil_parse", text) == ref(text)


def test_long_input_is_bounded_and_fast(db):
    # Over the 64 KiB limit: NULL, immediately
    big = "2003-09-25 " + "x" * 100_000
    assert db.execute("select dateutil_parse_fuzzy(?)", (big,)).fetchone()[0] is None
    # Under the limit: linear time, even for pathological token streams
    start = time.perf_counter()
    for text in ["1." * 30_000, "Jan " * 15_000, " " * 60_000, "a.b" * 20_000,
                 "10:" * 20_000, "9" * 60_000]:
        db.execute("select dateutil_parse_fuzzy(?)", (text,)).fetchone()
    assert time.perf_counter() - start < 1.0


def test_fuzzy_long_text_matches_dateutil(sql):
    text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 50
            + " The event is on March 3rd, 2021 at 4:15pm. " +
            "Sed do eiusmod tempor incididunt ut labore. " * 50)
    assert sql("dateutil_parse_fuzzy", text) == ref(text, fuzzy=True)


def test_works_in_queries(db):
    db.execute("create table t (s text)")
    db.executemany("insert into t values (?)", [("Sep 25 2003",), ("junk",), (None,)])
    rows = db.execute("select s, dateutil_parse(s) from t order by rowid").fetchall()
    assert rows == [("Sep 25 2003", "2003-09-25T00:00:00"), ("junk", None), (None, None)]
    # Results sort and compare correctly with SQLite's own date functions
    assert db.execute(
        "select date(dateutil_parse('Thu, 25 Sep 2003 10:49:41 -0300'))"
    ).fetchone()[0] == "2003-09-25"


def test_performance(db):
    texts = corpus(seed=99, n=20_000)
    db.execute("create temp table perf (s text)")
    db.executemany("insert into perf values (?)", [(t,) for t in texts])
    start = time.perf_counter()
    db.execute("select count(dateutil_parse_fuzzy(s)) from perf").fetchone()
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"{elapsed:.3f}s for 20,000 rows"


# --------------------------------------------------------------------------
# Documented, intentional differences from dateutil

def test_difference_local_timezone_names(sql, monkeypatch):
    """dateutil maps names in time.tzname to tzlocal(); we don't, so the
    result never depends on the server's TZ setting. With TZ=UTC the only
    such name is UTC, which both treat as +00:00."""
    assert sql("dateutil_parse", "2003-09-25 10:00 UTC") == "2003-09-25T10:00:00+00:00"
    assert sql("dateutil_parse", "2003-09-25 10:00 EST") == "2003-09-25T10:00:00"


def test_difference_non_ascii_digits(sql):
    """Only ASCII digits form numbers. Python's float() and int() also accept
    other Unicode decimal digits, such as full-width ones."""
    text = "\uff12\uff10\uff10\uff13-01-02"   # full-width "2003"
    assert ref(text) == "2003-01-02T00:00:00"
    assert sql("dateutil_parse", text) is None


def test_unicode_punctuation_matches_dateutil(sql):
    """Non-ASCII punctuation is classified exactly as Python does."""
    for text in ["25\u2013Jan\u20132003", "Jan\u20132003", "Jan\u2013Feb 2003",
                 "\u2018Sep 25\u2019 2003", "2003 \u00b7 Jan", "Sep 25\u20ac 2003",
                 "Jan of \u00b2", "Jan 5 2\u00b2", "e\u0301t\u00e9 Sep 25 2003",
                 "\U0001F4C5 2003-09-25 \U0001F600"]:
        for fn, kwargs in [("dateutil_parse", {}), ("dateutil_parse_fuzzy", {"fuzzy": True})]:
            assert sql(fn, text) == ref(text, **kwargs), (fn, text)
