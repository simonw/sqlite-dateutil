"""
Property-based differential tests with Hypothesis.

Every property compares the SQL functions against dateutil itself, so a
failure is either a fidelity bug or a crash. Hypothesis shrinks failing
inputs to a minimal example and remembers them in .hypothesis/.

    uv run pytest tests/test_properties.py                       # quick
    HYPOTHESIS_PROFILE=thorough uv run pytest tests/test_properties.py
"""
import datetime
import os
import re
import unicodedata

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from conftest import VARIANTS, ref
from test_dateutil_parse import TOKENS

settings.register_profile(
    "dateutil", max_examples=300, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("thorough", parent=settings.get_profile("dateutil"),
                          max_examples=20_000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dateutil"))

# --------------------------------------------------------------------------
# Known, documented differences (see README) are excluded with assume().

LONG_DIGITS = re.compile(r"\d{20,}")


def in_scope(text):
    # 1. Only ASCII digits form numbers here; Python accepts any Unicode
    #    decimal digit (e.g. full-width '２').
    if any(ord(c) > 127 and unicodedata.decimal(c, None) is not None for c in text):
        return False
    # 2. dateutil's Decimal arithmetic has 28 significant digits; very long
    #    numbers may round differently or raise InvalidOperation.
    if LONG_DIGITS.search(text):
        return False
    return True


# --------------------------------------------------------------------------
# Strategies

variants = st.sampled_from(sorted(VARIANTS))

# Arbitrary Unicode text, minus surrogates (not encodable as UTF-8)
any_text = st.text(st.characters(exclude_categories=["Cs"]), max_size=60)

# Mostly-ASCII text biased toward characters the tokenizer cares about
date_chars = st.text(
    st.sampled_from("0123456789     :-/.,+()TZamphAPMJanFebSepUTCGMT\t\x00\xa0é–"),
    max_size=40,
)

numbers = st.one_of(
    st.integers(0, 10**8).map(str),
    st.integers(0, 99999).map(lambda n: f"{n:0{len(str(n)) + 1}}"),   # leading zero
    st.tuples(st.integers(0, 99), st.integers(0, 10**6)).map(lambda t: f"{t[0]}.{t[1]}"),
)
token_soup = st.lists(st.one_of(st.sampled_from(TOKENS), numbers),
                      min_size=1, max_size=12).map("".join)

FORMATS = [
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S%z", "%Y%m%dT%H%M%S", "%Y%m%d", "%Y-%m-%d",
    "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%m/%d/%y %I:%M %p",
    "%a, %d %b %Y %H:%M:%S %z", "%a %b %d %H:%M:%S %Y", "%A, %B %d, %Y",
    "%d %B %Y", "%B %d %Y %I%p", "%b-%d-%Y", "%Y %b %d", "%H:%M", "%I:%M:%S %p",
    "%d-%b-%y", "%B of %Y", "%d of %B",
]
offsets = st.integers(-23 * 60 - 59, 23 * 60 + 59).map(
    lambda m: datetime.timezone(datetime.timedelta(minutes=m)))
moments = st.datetimes(
    min_value=datetime.datetime(1, 1, 1), max_value=datetime.datetime(9999, 12, 31),
    timezones=st.one_of(st.none(), offsets),
)


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def strftime(dt, fmt):
    """Platform- and locale-independent subset of strftime. datetime.strftime
    defers to the C library, which differs across platforms (Windows raises
    ValueError for some inputs; glibc doesn't zero-pad %Y below 1000)."""
    def offset():
        off = dt.utcoffset()
        if off is None:
            return ""
        mins = int(off.total_seconds()) // 60
        sign = "-" if mins < 0 else "+"
        return f"{sign}{abs(mins) // 60:02}{abs(mins) % 60:02}"

    codes = {
        "Y": f"{dt.year:04}", "y": f"{dt.year % 100:02}", "m": f"{dt.month:02}",
        "d": f"{dt.day:02}", "H": f"{dt.hour:02}", "M": f"{dt.minute:02}",
        "S": f"{dt.second:02}", "f": f"{dt.microsecond:06}",
        "I": f"{(dt.hour % 12) or 12:02}", "p": "AM" if dt.hour < 12 else "PM",
        "A": DAYS[dt.weekday()], "a": DAYS[dt.weekday()][:3],
        "B": MONTHS[dt.month - 1], "b": MONTHS[dt.month - 1][:3],
        "z": offset(), "%": "%",
    }
    return re.sub(r"%(.)", lambda m: codes[m.group(1)], fmt)


@st.composite
def formatted_dates(draw):
    """Real datetimes rendered in common formats, sometimes with noise."""
    moment = draw(moments)
    text = strftime(moment, draw(st.sampled_from(FORMATS)))
    if draw(st.booleans()):
        text = draw(st.sampled_from(["", " ", "on ", "Due: ", "("])) + text \
            + draw(st.sampled_from(["", " ", " UTC", " Z", " GMT+3", ")", ", ok", " EST"]))
    return text


@st.composite
def sentences(draw):
    """Fuzzy-mode input: a date hidden among words."""
    words = st.lists(st.sampled_from(
        ["the", "meeting", "is", "on", "at", "of", "and", "by", "a", "Today", "x7",
         "Room", "#4", "-", ",", "I", "will", "see", "you", "2nd", "floor", "Mar."]),
        max_size=6).map(" ".join)
    return f"{draw(words)} {draw(formatted_dates())} {draw(words)}"


all_inputs = st.one_of(any_text, date_chars, token_soup, formatted_dates(), sentences())

# --------------------------------------------------------------------------
# Properties


def check_matches(sql, fn, text):
    assume(in_scope(text))
    got, want = sql(fn, text), ref(text, **VARIANTS[fn])
    assert got == want, f"{fn}({text!r}): got {got!r}, dateutil gives {want!r}"
    return got


@given(fn=variants, text=all_inputs)
def test_matches_dateutil(sql, fn, text):
    check_matches(sql, fn, text)


@given(text=formatted_dates())
def test_formatted_dates_match_dateutil(sql, text):
    """Well-formed dates, strict mode: should almost always parse."""
    check_matches(sql, "dateutil_parse", text)
    check_matches(sql, "dateutil_parse_dayfirst", text)


@given(fn=st.sampled_from(["dateutil_parse_fuzzy", "dateutil_parse_fuzzy_dayfirst"]),
       text=sentences())
def test_fuzzy_sentences_match_dateutil(sql, fn, text):
    check_matches(sql, fn, text)


@given(fn=variants, text=all_inputs)
def test_output_is_iso_and_round_trips(sql, fn, text):
    """Any non-NULL result is valid ISO 8601 that parses back to itself."""
    assume(in_scope(text))
    got = sql(fn, text)
    if got is not None:
        datetime.datetime.fromisoformat(got)
        assert sql("dateutil_parse", got) == got


@given(fn=variants, data=st.binary(max_size=80))
def test_arbitrary_bytes_never_fail(db, fn, data):
    """BLOBs, invalid UTF-8 and NULs: result is NULL or a string, never an error."""
    (got,) = db.execute(f"select {fn}(?)", (data,)).fetchone()
    assert got is None or isinstance(got, str)
