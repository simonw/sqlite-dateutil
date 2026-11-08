// Install a Pyodide wheel with micropip, as a Pyodide user would, then check it:
//
//   cd tests/pyodide && npm ci && node test.mjs ../../dist/sqlite_dateutil-*-pyemscripten_2026_0_wasm32.whl
//
// The Pyodide release comes from the ABI in the wheel's tag (pyodide-<ABI> in
// package.json). Runs every hand-picked case in tests/test_dateutil_parse.py
// through all four SQL functions and compares them with python-dateutil
// running in the same Pyodide.
import { readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const wheel = process.argv[2];
const abi = wheel && basename(wheel).match(/-pyemscripten_(\d{4}_\d+)_wasm32\.whl$/)?.[1];
if (!abi) {
  console.error("usage: node test.mjs path/to/sqlite_dateutil-...-pyemscripten_<ABI>_wasm32.whl");
  process.exit(2);
}
const { loadPyodide } = await import(`pyodide-${abi}`);
const here = dirname(fileURLToPath(import.meta.url));
const cases = readFileSync(join(here, "..", "test_dateutil_parse.py"), "utf8");

const py = await loadPyodide();
console.log(`Pyodide ${py.version} (ABI ${abi})`);
py.FS.mkdirTree("/wheels");
const wheelPath = `/wheels/${basename(wheel)}`;
py.FS.writeFile(wheelPath, readFileSync(wheel));
py.FS.writeFile("/wheels/test_dateutil_parse.py", cases);

await py.loadPackage("micropip");
const micropip = py.pyimport("micropip");
await micropip.install(`emfs:${wheelPath}`); // checks the wheel's platform tag
await micropip.install("python-dateutil");

// In Pyodide 0.29, sqlite3 is a separate Pyodide package. Check that
// importing sqlite_dateutil without it explains what to do, then load it.
const sqlite3BuiltIn = py.runPython("import importlib.util; importlib.util.find_spec('_sqlite3') is not None");
if (!sqlite3BuiltIn) {
  const message = py.runPython(`
try:
    import sqlite_dateutil
except ModuleNotFoundError as e:
    message = str(e)
else:
    raise AssertionError("importing without sqlite3 should fail")
message`);
  if (!message.includes("micropip.install('sqlite3')")) throw new Error(`unhelpful error: ${message}`);
  py.runPython("import sys; [sys.modules.pop(m) for m in list(sys.modules) if m.startswith('sqlite_dateutil')]");
  await micropip.install("sqlite3");
  console.log("sqlite3 loaded separately, after checking the missing-sqlite3 error");
}

const result = py.runPython(`
import ast, sqlite3, warnings

opened_before_import = sqlite3.connect(":memory:")

import sqlite_dateutil
from dateutil.parser import parse

db = sqlite3.connect(":memory:")
sqlite_dateutil.load(db)

try:
    sqlite_dateutil.load(opened_before_import)
except RuntimeError as e:
    assert "imported before the connection" in str(e), e
else:
    raise AssertionError("load() should reject a connection opened before the import")

tree = ast.parse(open("/wheels/test_dateutil_parse.py").read())
cases = next(ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CASES")
variants = {
    "dateutil_parse": {},
    "dateutil_parse_fuzzy": {"fuzzy": True},
    "dateutil_parse_dayfirst": {"dayfirst": True},
    "dateutil_parse_fuzzy_dayfirst": {"fuzzy": True, "dayfirst": True},
}

def ref(text, **kwargs):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return parse(text, **kwargs).isoformat()
    except (ValueError, OverflowError, ArithmeticError):
        return None

mismatches = []
for fn, kwargs in variants.items():
    for case in cases:
        got = db.execute(f"select {fn}(?)", (case,)).fetchone()[0]
        expected = ref(case, **kwargs)
        if got != expected:
            mismatches.append((fn, case, got, expected))

assert not mismatches, f"{len(mismatches)} mismatches, first few: {mismatches[:5]}"
f"sqlite-dateutil {sqlite_dateutil.__version__} (SQLite {sqlite3.sqlite_version}): " \\
    f"{len(cases)} cases x {len(variants)} functions match python-dateutil"
`);
console.log(result);
