"""
Build the dateutil_parse SQLite extension (and the sanitizer test driver)
on macOS, Linux and Windows using only the standard library.

    python tools/build.py           # build the extension if out of date
    python tools/build.py --force   # always rebuild
    python tools/build.py --asan    # also build build/du_cli_asan
    python tools/build.py --fuzz    # also build build/fuzz_parse (libFuzzer)
    python tools/build.py --fuzz-replay   # build/fuzz_replay (any ASan compiler)

The test suite calls this automatically, so `uv run pytest` is enough.

Compiler selection: $CC if set, otherwise `cc` on Unix. On Windows: MSVC
`cl` if it is on PATH (run from a "Native Tools Command Prompt"), else
`gcc` or `clang` (MinGW / LLVM). Extra flags can be passed in $CFLAGS.
The SQLite headers come from vendor/sqlite, never from the system, because
some system copies (notably the macOS SDK's) disable the extension API.
"""
import argparse
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "sqlite"
BUILD = ROOT / "build"
SOURCES = [
    ROOT / "dateutil_parse.c",
    ROOT / "dateutil_unicode.h",
    VENDOR / "sqlite3.h",
    VENDOR / "sqlite3ext.h",
    pathlib.Path(__file__).resolve(),
]

if sys.platform == "win32":
    EXT_SUFFIX, EXE_SUFFIX = ".dll", ".exe"
elif sys.platform == "darwin":
    EXT_SUFFIX, EXE_SUFFIX = ".dylib", ""
else:
    EXT_SUFFIX, EXE_SUFFIX = ".so", ""

# Same path the README documents: `.load ./dateutil_parse`
EXT_PATH = ROOT / f"dateutil_parse{EXT_SUFFIX}"
ASAN_PATH = BUILD / f"du_cli_asan{EXE_SUFFIX}"
FUZZ_PATH = BUILD / f"fuzz_parse{EXE_SUFFIX}"
REPLAY_PATH = BUILD / f"fuzz_replay{EXE_SUFFIX}"
FUZZ_SOURCES = SOURCES + [ROOT / "fuzz" / "fuzz_parse.c"]
SANITIZE = ["-g", "-O1", "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]

WARNINGS = ["-Wall", "-Wextra", "-Wshadow", "-Wconversion", "-Wno-sign-conversion"]


class BuildError(Exception):
    pass


def find_compiler():
    """Return (argv prefix, is_msvc)."""
    cc = os.environ.get("CC")
    if cc:
        argv = shlex.split(cc, posix=(os.name != "nt"))
        name = pathlib.Path(argv[0]).name.lower()
        return argv, name in ("cl", "cl.exe", "clang-cl", "clang-cl.exe")
    candidates = ["cl", "gcc", "clang"] if sys.platform == "win32" else ["cc", "gcc", "clang"]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return [path], name == "cl"
    if sys.platform == "win32":
        hint = ("Install Visual Studio Build Tools and run from an "
                "'x64 Native Tools Command Prompt', or put MinGW-w64 gcc / "
                "LLVM clang on PATH, or set CC.")
    elif sys.platform == "darwin":
        hint = "Run `xcode-select --install`, or set CC."
    else:
        hint = "Install gcc or clang (e.g. `apt install build-essential`), or set CC."
    raise BuildError("No C compiler found. " + hint)


def _stale(target, deps):
    if not target.exists():
        return True
    t = target.stat().st_mtime
    return any(d.stat().st_mtime > t for d in deps)


def _run(argv):
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(
            "Build failed:\n  " + " ".join(map(str, argv)) + "\n"
            + proc.stdout + proc.stderr
        )
    return proc


def build_extension(force=False, verbose=False):
    if not force and not _stale(EXT_PATH, SOURCES):
        return EXT_PATH
    cc, msvc = find_compiler()
    extra = shlex.split(os.environ.get("CFLAGS", ""), posix=(os.name != "nt"))
    src = str(ROOT / "dateutil_parse.c")
    if msvc:
        BUILD.mkdir(exist_ok=True)
        argv = cc + ["/nologo", "/O2", "/W3", "/LD", f"/I{VENDOR}", *extra, src,
                     f"/Fo{BUILD}\\", f"/Fe{EXT_PATH}",
                     "/link", f"/IMPLIB:{BUILD / 'dateutil_parse.lib'}"]
    else:
        shared = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared"]
        pic = [] if sys.platform == "win32" else ["-fPIC"]
        argv = cc + ["-O2", *WARNINGS, f"-I{VENDOR}", *pic, *shared, *extra,
                     src, "-o", str(EXT_PATH)]
    if verbose:
        print(" ".join(map(str, argv)))
    _run(argv)
    return EXT_PATH


def build_asan(force=False, verbose=False):
    """Build the SQLite-free parser driver under ASan + UBSan (gcc/clang only)."""
    deps = SOURCES + [ROOT / "tools" / "du_cli.c"]
    if not force and not _stale(ASAN_PATH, deps):
        return ASAN_PATH
    cc, msvc = find_compiler()
    if msvc:
        raise BuildError("The sanitizer driver needs gcc or clang (MSVC has no UBSan).")
    BUILD.mkdir(exist_ok=True)
    argv = cc + SANITIZE + [str(ROOT / "tools" / "du_cli.c"), "-o", str(ASAN_PATH)]
    if verbose:
        print(" ".join(map(str, argv)))
    _run(argv)
    return ASAN_PATH


def find_fuzz_compiler():
    """A clang that ships libFuzzer: $FUZZ_CC, Homebrew LLVM, or clang on PATH.
    (Apple's clang does not include libFuzzer.)"""
    if os.environ.get("FUZZ_CC"):
        return shlex.split(os.environ["FUZZ_CC"])
    for prefix in ("/opt/homebrew/opt/llvm", "/usr/local/opt/llvm"):
        clang = pathlib.Path(prefix) / "bin" / "clang"
        if sys.platform == "darwin" and clang.exists():
            return [str(clang)]
    clang = shutil.which("clang")
    if clang is None:
        raise BuildError("libFuzzer needs clang (on macOS: `brew install llvm`), or set FUZZ_CC.")
    return [clang]


def build_fuzz(force=False, verbose=False):
    """Build the libFuzzer harness fuzz/fuzz_parse.c with ASan + UBSan."""
    if not force and not _stale(FUZZ_PATH, FUZZ_SOURCES):
        return FUZZ_PATH
    BUILD.mkdir(exist_ok=True)
    argv = find_fuzz_compiler() + SANITIZE + [
        "-fsanitize=fuzzer", str(ROOT / "fuzz" / "fuzz_parse.c"), "-o", str(FUZZ_PATH)]
    if verbose:
        print(" ".join(map(str, argv)))
    _run(argv)
    return FUZZ_PATH


def build_fuzz_replay(force=False, verbose=False):
    """Build fuzz/replay.c: runs the harness on files, no libFuzzer needed."""
    deps = FUZZ_SOURCES + [ROOT / "fuzz" / "replay.c"]
    if not force and not _stale(REPLAY_PATH, deps):
        return REPLAY_PATH
    cc, msvc = find_compiler()
    if msvc:
        raise BuildError("The replay driver needs gcc or clang.")
    BUILD.mkdir(exist_ok=True)
    argv = cc + SANITIZE + [str(ROOT / "fuzz" / "replay.c"), "-o", str(REPLAY_PATH)]
    if verbose:
        print(" ".join(map(str, argv)))
    _run(argv)
    return REPLAY_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild even if up to date")
    parser.add_argument("--asan", action="store_true", help="also build the sanitizer driver")
    parser.add_argument("--fuzz", action="store_true", help="also build the libFuzzer harness")
    parser.add_argument("--fuzz-replay", action="store_true",
                        help="also build the fuzz input replay driver")
    args = parser.parse_args()
    try:
        print(build_extension(force=args.force, verbose=True))
        if args.asan:
            print(build_asan(force=args.force, verbose=True))
        if args.fuzz:
            print(build_fuzz(force=args.force, verbose=True))
        if args.fuzz_replay:
            print(build_fuzz_replay(force=args.force, verbose=True))
    except BuildError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
