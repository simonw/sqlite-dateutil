"""
Package the compiled extension as Python wheels, using only the standard library.

    python tools/build_wheels.py              # sqlite-dateutil wheel for this platform
    python tools/build_wheels.py --plugins    # datasette-/sqlite-utils- plugin wheels
    python tools/build_wheels.py --pyodide    # Pyodide wheel, latest ABI (needs emcc on PATH)
    python tools/build_wheels.py --pyodide 2025_0   # Pyodide 0.28/0.29 (Emscripten 4.0.9)
    python tools/build_wheels.py --plat macosx_10_13_x86_64 --binary path/to/dateutil_parse.dylib

Wheels are written to dist/ (override with --out). Names, version, summary,
license and URLs come from pyproject.toml; the long description is README.md.

The extension is a plain SQLite loadable library, not a CPython extension
module, so a single py3-none-<platform> wheel serves every Python 3 version.

Platform tags: macOS uses $MACOSX_DEPLOYMENT_TARGET (default 11.0 on arm64,
10.13 on x86_64), so compile with the same value. Linux defaults to
manylinux_2_28, which is only truthful if the binary was compiled on glibc
2.28 or older (the release workflow builds in the manylinux_2_28 images).
"""
import argparse
import base64
import hashlib
import io
import os
import pathlib
import platform
import subprocess
import sys
import time
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build  # noqa: E402  (tools/build.py)

# Fixed timestamp (or $SOURCE_DATE_EPOCH) so rebuilding gives identical wheels.
_EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", 315532800))  # 1980-01-01
ZIP_DATE = max(time.gmtime(_EPOCH)[:6], (1980, 1, 1, 0, 0, 0))

BASE_INIT_PY = '''\
"""Python helpers for the sqlite-dateutil SQLite extension."""
import os.path
import sys

try:
    import sqlite3
except ModuleNotFoundError as e:
    if sys.platform == "emscripten":
        raise ModuleNotFoundError(
            "sqlite-dateutil needs Pyodide's sqlite3 package: run "
            "`await micropip.install('sqlite3')` (or pyodide.loadPackage('sqlite3') "
            "from JavaScript) before importing sqlite_dateutil"
        ) from e
    raise

__version__ = "{version}"
__version_info__ = tuple(__version__.split("."))


def loadable_path() -> str:
    """Path to the bundled extension, without its file suffix (as SQLite expects)."""
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "{stem}"))


if sys.platform == "emscripten":
    # Pyodide's sqlite3 module is built without load_extension(). Its SQLite
    # is still reachable with ctypes, so register the extension with
    # sqlite3_auto_extension(): every connection opened after this import
    # gets the functions. Pyodide 314+ links SQLite into the main module;
    # older releases ship it inside the separate _sqlite3 shared library.
    #
    # SQLite passes the init function its table of API functions, but a
    # SQLite built without load-extension support (Pyodide 0.28/0.29) passes
    # NULL. So hand the extension the address of every function it uses
    # first; tools/pyodide_shim.c falls back to those when the table is NULL.
    import ctypes

    def _find_sqlite():
        import _sqlite3

        candidates = [None]
        if getattr(_sqlite3, "__file__", None):
            candidates.append(_sqlite3.__file__)
        for candidate in candidates:
            lib = ctypes.CDLL(candidate)
            if hasattr(lib, "sqlite3_auto_extension"):
                return lib
        raise ImportError("sqlite-dateutil could not find SQLite's C API in this Pyodide")

    def _address(func):
        return ctypes.cast(func, ctypes.c_void_p).value

    _sqlite = _find_sqlite()
    _lib = ctypes.CDLL(loadable_path() + "{suffix}")
    _lib.dateutil_pyodide_set_api.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    _lib.dateutil_pyodide_set_api.restype = ctypes.c_int
    _names = ctypes.string_at(  # a C char array, so take its address
        ctypes.addressof(ctypes.c_char.in_dll(_lib, "dateutil_pyodide_api_names")))
    for _name in _names.decode().strip(",").split(","):
        _fn = getattr(_sqlite, "sqlite3_" + _name, None)
        if _fn is None or _lib.dateutil_pyodide_set_api(_name.encode(), _address(_fn)) != 0:
            raise ImportError("sqlite-dateutil could not find sqlite3_" + _name)
    _sqlite.sqlite3_auto_extension.argtypes = [ctypes.c_void_p]
    _sqlite.sqlite3_auto_extension.restype = ctypes.c_int
    _rc = _sqlite.sqlite3_auto_extension(_address(_lib.dateutil_pyodide_init))
    if _rc != 0:
        raise ImportError("sqlite3_auto_extension() failed with code %d" % _rc)


def load(conn: sqlite3.Connection) -> None:
    """Load the extension into conn.

    Extension loading is switched on for the call and back off afterwards.
    In Pyodide the extension is registered when this module is imported, so
    connections opened after the import already have the functions.
    """
    if sys.platform == "emscripten":
        try:
            conn.execute("select dateutil_parse('')")
        except sqlite3.OperationalError:
            raise RuntimeError(
                "In Pyodide, sqlite_dateutil must be imported before the "
                "connection is opened: this connection predates the import"
            ) from None
        return
    conn.enable_load_extension(True)
    try:
        conn.load_extension(loadable_path())
    finally:
        conn.enable_load_extension(False)
'''

# Pyodide: a wheel only runs on the Pyodide ABI it was built for, and each
# ABI pins one exact Emscripten version. Keep this in step with the Pyodide
# matrix in .github/workflows/test.yml and tests/pyodide/package.json.
PYODIDE_TARGETS = {
    # ABI: (Emscripten, wheel platform tag, Pyodide releases)
    # 2025_0 uses the older pyodide_ tag: micropip in Pyodide 0.28 rejects the
    # PEP 783 pyemscripten_2025_0 tag (0.29 accepts both).
    "2025_0": ("4.0.9", "pyodide_2025_0_wasm32", "0.28/0.29"),
    "2026_0": ("5.0.3", "pyemscripten_2026_0_wasm32", "314"),  # PEP 783 tag
}
PYODIDE_LATEST = "2026_0"

PLUGIN_INIT_PY = '''\
from {framework_module} import hookimpl
import sqlite_dateutil

__version__ = "{version}"


@hookimpl
def prepare_connection(conn):
    sqlite_dateutil.load(conn)
'''

PLUGINS = {
    # distribution prefix: (framework module, entry point group, framework label)
    "datasette": ("datasette", "datasette", "Datasette"),
    "sqlite-utils": ("sqlite_utils", "sqlite_utils", "sqlite-utils"),
}


def read_project():
    with open(ROOT / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    for key in ("name", "version", "description", "license"):
        if key not in project:
            sys.exit(f"pyproject.toml [project] needs a {key!r} for the wheels")
    return project


def macos_deployment_target():
    """$MACOSX_DEPLOYMENT_TARGET, defaulting to the oldest macOS worth supporting."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    return os.environ.get("MACOSX_DEPLOYMENT_TARGET") or ("11.0" if arm else "10.13")


def macos_minos(binary):
    """Minimum macOS version recorded in a Mach-O file, as a tuple, or None."""
    try:
        out = subprocess.run(["otool", "-l", str(binary)], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    words = out.split()
    for marker in ("minos", "version"):  # LC_BUILD_VERSION, then LC_VERSION_MIN_MACOSX
        if marker in words:
            return tuple(int(p) for p in words[words.index(marker) + 1].split("."))
    return None


def check_macos_tag(binary, tag):
    """Refuse to label a binary as running on an older macOS than it was built for."""
    if not tag.startswith("macosx_"):
        return
    minos = macos_minos(binary)
    tag_version = tuple(int(p) for p in tag.split("_")[1:3])
    if minos and minos[:2] > tag_version:
        sys.exit(
            f"{binary} needs macOS {'.'.join(map(str, minos))} but the wheel tag "
            f"{tag} claims {tag_version[0]}.{tag_version[1]}. Rebuild with "
            f"MACOSX_DEPLOYMENT_TARGET={tag_version[0]}.{tag_version[1]}.")


def default_platform_tag():
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        major, _, minor = macos_deployment_target().partition(".")
        return f"macosx_{major}_{minor or 0}_{arch}"
    if sys.platform == "win32":
        return {"amd64": "win_amd64", "x86_64": "win_amd64", "arm64": "win_arm64"}[machine]
    if sys.platform.startswith("linux"):
        arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
        return f"manylinux_2_28_{arch}"
    sys.exit(f"No default wheel tag for {sys.platform}/{machine}; pass --plat")


def build_pyodide_binary(abi):
    """Compile the extension as an Emscripten side module that Pyodide can dlopen."""
    emscripten = PYODIDE_TARGETS[abi][0]
    emcc = os.environ.get("EMCC", "emcc")
    try:
        version = subprocess.run([emcc, "--version"], capture_output=True, text=True,
                                 check=True).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        sys.exit(f"Pyodide ABI {abi} needs Emscripten {emscripten}: `{emcc}` not found. "
                 f"Install it with emsdk (`./emsdk install {emscripten}`).")
    if f" {emscripten} " not in version:
        sys.exit(f"Pyodide ABI {abi} needs Emscripten {emscripten}, "
                 f"but `{emcc} --version` says: {version}")
    out = build.BUILD / f"pyodide-{abi}" / "dateutil_parse.so"
    out.parent.mkdir(parents=True, exist_ok=True)
    # pyodide_shim.c adds the entry point that copes with Pyodide's SQLite
    # (see the emscripten branch of BASE_INIT_PY).
    argv = [emcc, "-O2", "-fPIC", "-sSIDE_MODULE=1", *build.WARNINGS,
            f"-I{build.VENDOR}", str(ROOT / "dateutil_parse.c"),
            str(ROOT / "tools" / "pyodide_shim.c"), "-o", str(out)]
    print(" ".join(argv))
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit("Build failed:\n" + proc.stdout + proc.stderr)
    return out


def normalize(name):
    return name.replace("-", "_").lower()


class Wheel:
    def __init__(self, name, version, tag):
        self.name, self.version, self.tag = name, version, tag
        self.dist_info = f"{normalize(name)}-{version}.dist-info"
        self.filename = f"{normalize(name)}-{version}-{tag}.whl"
        self.buffer = io.BytesIO()
        self.zip = zipfile.ZipFile(self.buffer, "w", zipfile.ZIP_DEFLATED)
        self.record = []

    def add(self, path, data, executable=False):
        if isinstance(data, str):
            data = data.encode("utf-8")
        info = zipfile.ZipInfo(path, ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100755 if executable else 0o100644) << 16
        self.zip.writestr(info, data)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        self.record.append(f"{path},sha256={digest.decode()},{len(data)}")

    def finish(self, out_dir, metadata, purelib, entry_points=None):
        self.add(f"{self.dist_info}/METADATA", metadata)
        self.add(f"{self.dist_info}/WHEEL", (
            "Wheel-Version: 1.0\n"
            "Generator: sqlite-dateutil tools/build_wheels.py\n"
            f"Root-Is-Purelib: {'true' if purelib else 'false'}\n"
            f"Tag: {self.tag}\n"))
        if entry_points:
            self.add(f"{self.dist_info}/entry_points.txt", entry_points)
        record_path = f"{self.dist_info}/RECORD"
        self.record.append(f"{record_path},,")
        info = zipfile.ZipInfo(record_path, ZIP_DATE)
        info.external_attr = 0o100644 << 16
        self.zip.writestr(info, "\n".join(self.record) + "\n", zipfile.ZIP_DEFLATED)
        self.zip.close()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / self.filename
        path.write_bytes(self.buffer.getvalue())
        return path


def metadata(project, name, summary, requires=(), description=None):
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {project['version']}",
        f"Summary: {summary}",
    ]
    for author in project.get("authors", []):
        if "name" in author:
            lines.append(f"Author: {author['name']}")
    lines.append(f"License-Expression: {project['license']}")
    if "requires-python" in project:
        lines.append(f"Requires-Python: {project['requires-python']}")
    for label, url in project.get("urls", {}).items():
        lines.append(f"Project-URL: {label}, {url}")
    lines += [f"Requires-Dist: {r}" for r in requires]
    if description is not None:
        lines.append("Description-Content-Type: text/markdown")
        return "\n".join(lines) + "\n\n" + description
    return "\n".join(lines) + "\n"


def build_base_wheel(project, binary, tag, out_dir):
    check_macos_tag(binary, tag)
    wheel = Wheel(project["name"], project["version"], f"py3-none-{tag}")
    pkg = normalize(project["name"])
    wheel.add(f"{pkg}/__init__.py", BASE_INIT_PY.format(
        version=project["version"], stem=binary.stem, suffix=binary.suffix))
    wheel.add(f"{pkg}/{binary.name}", binary.read_bytes(), executable=True)
    readme = (ROOT / project.get("readme", "README.md")).read_text("utf-8")
    return wheel.finish(out_dir, metadata(
        project, project["name"], project["description"], description=readme),
        purelib=False)


PLUGIN_FUNCTIONS = """\
| SQL function | Python equivalent |
| --- | --- |
| `dateutil_parse(text)` | `parse(text).isoformat()` |
| `dateutil_parse_fuzzy(text)` | `parse(text, fuzzy=True).isoformat()` |
| `dateutil_parse_dayfirst(text)` | `parse(text, dayfirst=True).isoformat()` |
| `dateutil_parse_fuzzy_dayfirst(text)` | `parse(text, fuzzy=True, dayfirst=True).isoformat()` |
"""

PLUGIN_EXAMPLES = {
    "datasette": """\
Once installed, the functions are available in every SQL query, for example:

```sql
select dateutil_parse('Thu, 25 Sep 2003 10:49:41 -0300');
-- 2003-09-25T10:49:41-03:00
```
""",
    "sqlite-utils": """\
Once installed, the functions are available to `sqlite-utils query` and `sqlite-utils memory`:

```bash
sqlite-utils memory "select dateutil_parse_fuzzy('Today is January 1, 2047 at 8:21:00AM') as d"
# [{"d": "2047-01-01T08:21:00"}]
```

They are also available to Python code that uses `sqlite_utils.Database()`.
""",
}


def plugin_description(project, prefix, name, summary):
    repo = project.get("urls", {}).get("Homepage") or project["name"]
    return (
        f"# {name}\n\n"
        f"{summary}. [{project['name']}]({repo}) is a C port of "
        "[python-dateutil](https://github.com/dateutil/dateutil)'s `parser.parse()`, "
        "about 38x faster than calling dateutil from Python.\n\n"
        f"## Installation\n\n```bash\n{prefix} install {name}\n```\n\n"
        f"## Usage\n\n{PLUGIN_EXAMPLES[prefix]}\n"
        "Each function returns an ISO 8601 string, or `NULL` where dateutil would raise an "
        "exception:\n\n"
        f"{PLUGIN_FUNCTIONS}\n"
        f"See the [{project['name']} documentation]({repo}) for details and the known "
        "differences from dateutil.\n")


def build_plugin_wheel(project, prefix, out_dir):
    framework_module, group, label = PLUGINS[prefix]
    name = f"{prefix}-{project['name']}"
    wheel = Wheel(name, project["version"], "py3-none-any")
    pkg = normalize(name)
    wheel.add(f"{pkg}/__init__.py", PLUGIN_INIT_PY.format(
        framework_module=framework_module, version=project["version"]))
    summary = f"{label} plugin that loads the {project['name']} SQLite extension"
    description = plugin_description(project, prefix, name, summary)
    return wheel.finish(out_dir, metadata(
        project, name, summary,
        requires=[prefix, f"{project['name']}=={project['version']}"],
        description=description),
        purelib=True, entry_points=f"[{group}]\n{normalize(project['name'])} = {pkg}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "dist",
                        help="output directory (default: dist/)")
    parser.add_argument("--binary", type=pathlib.Path,
                        help="prebuilt extension to package (default: build it with tools/build.py)")
    parser.add_argument("--plat", help="wheel platform tag (default: detect from this machine)")
    parser.add_argument("--plugins", action="store_true",
                        help="build the Datasette and sqlite-utils plugin wheels instead")
    parser.add_argument(
        "--pyodide", nargs="?", const=PYODIDE_LATEST, choices=sorted(PYODIDE_TARGETS),
        metavar="ABI",
        help="build the Pyodide wheel for this Pyodide ABI (default %(const)s); needs that "
             "ABI's emcc on PATH: " + ", ".join(
                 f"{abi} (Pyodide {t[2]}, Emscripten {t[0]})"
                 for abi, t in sorted(PYODIDE_TARGETS.items())))
    args = parser.parse_args()
    project = read_project()
    if args.plugins:
        for prefix in PLUGINS:
            print(build_plugin_wheel(project, prefix, args.out))
        return
    if args.pyodide:
        binary = args.binary or build_pyodide_binary(args.pyodide)
        tag = args.plat or PYODIDE_TARGETS[args.pyodide][1]
        print(build_base_wheel(project, binary, tag, args.out))
        return
    if args.binary:
        binary = args.binary
    else:
        if sys.platform == "darwin":
            # clang reads this; without it the binary targets the build machine's macOS.
            os.environ["MACOSX_DEPLOYMENT_TARGET"] = macos_deployment_target()
        try:
            # Always rebuild: an existing binary may have been built with other settings.
            binary = build.build_extension(force=True, verbose=True)
        except build.BuildError as e:
            sys.exit(str(e))
    print(build_base_wheel(project, binary, args.plat or default_platform_tag(), args.out))


if __name__ == "__main__":
    main()
