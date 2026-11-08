import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import warnings

import pytest

# Pin the process time zone *before* anything computes "now". dateutil
# maps tz names in time.tzname to the local zone, which the extension
# deliberately does not do; with TZ=UTC the only such name is "UTC",
# which both treat as +00:00.
os.environ["TZ"] = "UTC"
time.tzset()

from dateutil.parser import parse  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT_SUFFIX = "dylib" if sys.platform == "darwin" else "so"
EXT_PATH = ROOT / f"dateutil_parse.{EXT_SUFFIX}"

VARIANTS = {
    "dateutil_parse": {},
    "dateutil_parse_fuzzy": {"fuzzy": True},
    "dateutil_parse_dayfirst": {"dayfirst": True},
    "dateutil_parse_fuzzy_dayfirst": {"fuzzy": True, "dayfirst": True},
}

# Flag values understood by tools/du_cli.c
FLAGS = {"fuzzy": 1, "dayfirst": 2}


def ref(text, default=None, **kwargs):
    """dateutil.parser.parse(...).isoformat(), or None where it raises."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # UnknownTimezoneWarning
            return parse(text, default=default, **kwargs).isoformat()
    except (ValueError, OverflowError, ArithmeticError):
        # ParserError is a ValueError; isoformat() raises ValueError for
        # offsets of 24h or more; decimal.InvalidOperation is an
        # ArithmeticError raised for absurdly long numbers.
        return None


def _build():
    src = ROOT / "dateutil_parse.c"
    if not EXT_PATH.exists() or EXT_PATH.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["make", "-C", str(ROOT)], check=True)


@pytest.fixture(scope="session")
def db():
    _build()
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    conn.load_extension(str(EXT_PATH.with_suffix("")))
    conn.enable_load_extension(False)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def sql(db):
    def run(fn, value):
        return db.execute(f"select {fn}(?)", (value,)).fetchone()[0]

    return run
