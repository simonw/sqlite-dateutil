"""Tests for tools/build_wheels.py: the wheels are well-formed and actually work."""
import base64
import email.parser
import hashlib
import importlib
import sqlite3
import sys
import zipfile

import pytest

import build
import build_wheels


@pytest.fixture(scope="module")
def project():
    return build_wheels.read_project()


def read_wheel(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def check_record(files):
    """Every file is listed in RECORD with the right hash and size, and nothing else is."""
    (record_name,) = [n for n in files if n.endswith(".dist-info/RECORD")]
    rows = [line.split(",") for line in files[record_name].decode().splitlines()]
    assert {row[0] for row in rows} == set(files)
    for name, digest, size in rows:
        if name == record_name:
            assert (digest, size) == ("", "")
            continue
        expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=")
        assert digest == "sha256=" + expected.decode()
        assert int(size) == len(files[name])


def metadata(files):
    (name,) = [n for n in files if n.endswith(".dist-info/METADATA")]
    return email.parser.Parser().parsestr(files[name].decode())


def test_base_wheel(project, tmp_path):
    # A non-macOS tag skips the deployment-target check, which the dev build
    # (compiled for this machine's macOS) would otherwise fail.
    path = build_wheels.build_base_wheel(project, build.EXT_PATH, "linux_x86_64", tmp_path)
    assert path.name == f"sqlite_dateutil-{project['version']}-py3-none-linux_x86_64.whl"
    files = read_wheel(path)
    check_record(files)
    assert f"sqlite_dateutil/{build.EXT_PATH.name}" in files
    meta = metadata(files)
    assert meta["Name"] == "sqlite-dateutil"
    assert meta["Version"] == project["version"]
    assert meta["License-Expression"] == project["license"]
    assert meta["Summary"] == project["description"]
    assert "dateutil_parse(" in meta.get_payload()  # README is the long description
    wheel = files[f"sqlite_dateutil-{project['version']}.dist-info/WHEEL"].decode()
    assert "Root-Is-Purelib: false" in wheel
    assert "Tag: py3-none-linux_x86_64" in wheel


def test_base_wheel_loads(project, tmp_path, monkeypatch):
    """Unpack the wheel as an installer would and use the package from it."""
    path = build_wheels.build_base_wheel(project, build.EXT_PATH, "linux_x86_64", tmp_path)
    site = tmp_path / "site-packages"
    with zipfile.ZipFile(path) as zf:
        zf.extractall(site)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(sys.modules, "sqlite_dateutil", raising=False)
    mod = importlib.import_module("sqlite_dateutil")
    try:
        assert mod.__file__.startswith(str(site))
        assert mod.__version__ == project["version"]
        conn = sqlite3.connect(":memory:")
        mod.load(conn)  # must not need enable_load_extension(True) first
        got = conn.execute("select dateutil_parse('2003-09-25T10:49:41')").fetchone()[0]
        assert got == "2003-09-25T10:49:41"
        with pytest.raises(sqlite3.OperationalError):
            conn.load_extension(mod.loadable_path())  # loading is switched off again
    finally:
        sys.modules.pop("sqlite_dateutil", None)


@pytest.mark.parametrize("prefix,group,module", [
    ("datasette", "datasette", "datasette"),
    ("sqlite-utils", "sqlite_utils", "sqlite_utils"),
])
def test_plugin_wheels(project, tmp_path, prefix, group, module):
    path = build_wheels.build_plugin_wheel(project, prefix, tmp_path)
    pkg = f"{prefix.replace('-', '_')}_sqlite_dateutil"
    assert path.name == f"{pkg}-{project['version']}-py3-none-any.whl"
    files = read_wheel(path)
    check_record(files)
    meta = metadata(files)
    assert meta["Name"] == f"{prefix}-sqlite-dateutil"
    assert meta.get_all("Requires-Dist") == [prefix, f"sqlite-dateutil=={project['version']}"]
    dist_info = f"{pkg}-{project['version']}.dist-info"
    assert files[f"{dist_info}/entry_points.txt"].decode() == f"[{group}]\nsqlite_dateutil = {pkg}\n"
    init = files[f"{pkg}/__init__.py"].decode()
    assert f"from {module} import hookimpl" in init
    compile(init, "__init__.py", "exec")


def test_wheels_are_reproducible(project, tmp_path):
    a = build_wheels.build_base_wheel(project, build.EXT_PATH, "linux_x86_64", tmp_path / "a")
    b = build_wheels.build_base_wheel(project, build.EXT_PATH, "linux_x86_64", tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O deployment target check")
def test_rejects_macos_tag_older_than_binary(project, tmp_path):
    minos = build_wheels.macos_minos(build.EXT_PATH)
    if minos is None:
        pytest.skip("otool not available")
    if minos[:2] <= (10, 9):
        pytest.skip("binary already targets an ancient macOS")
    with pytest.raises(SystemExit, match="MACOSX_DEPLOYMENT_TARGET"):
        build_wheels.build_base_wheel(project, build.EXT_PATH, "macosx_10_9_x86_64", tmp_path)
