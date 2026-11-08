/*
 * Pyodide-only entry point, compiled together with dateutil_parse.c by
 * `tools/build_wheels.py --pyodide`.
 *
 * In Pyodide the extension is registered with sqlite3_auto_extension()
 * through ctypes (see BASE_INIT_PY in tools/build_wheels.py). SQLite then
 * calls the init function with its table of API function pointers, except
 * that a SQLite built with SQLITE_OMIT_LOAD_EXTENSION (as in Pyodide 0.28
 * and 0.29) passes NULL. So Python looks up each function this extension
 * uses with ctypes, hands the addresses over through dateutil_pyodide_set_api(),
 * and dateutil_pyodide_init() falls back to that table when SQLite passes NULL.
 */
#include <string.h>

#include "sqlite3ext.h"
SQLITE_EXTENSION_INIT3 /* sqlite3_api itself is defined in dateutil_parse.c */

int sqlite3_dateutilparse_init(sqlite3 *db, char **pzErrMsg,
                               const sqlite3_api_routines *pApi);

/* Every sqlite3_* function dateutil_parse.c calls. Keep in sync with it. */
#define DATEUTIL_API(X)                                                        \
  X(create_function)                                                           \
  X(free)                                                                      \
  X(malloc64)                                                                  \
  X(result_error_nomem)                                                        \
  X(result_text)                                                               \
  X(user_data)                                                                 \
  X(value_bytes)                                                               \
  X(value_text)                                                                \
  X(value_type)

#define NAME(f) #f ","
/* Comma-separated, read by Python: "create_function,free,...," */
const char dateutil_pyodide_api_names[] = DATEUTIL_API(NAME);

static sqlite3_api_routines fallback_api;

/* Store the address of sqlite3_<name>. Returns 0, or 1 for an unknown name. */
int dateutil_pyodide_set_api(const char *name, void *fn) {
#define SET(f)                                                                 \
  if (strcmp(name, #f) == 0) {                                                 \
    memcpy(&fallback_api.f, &fn, sizeof(fallback_api.f));                      \
    return 0;                                                                  \
  }
  DATEUTIL_API(SET)
  return 1;
}

int dateutil_pyodide_init(sqlite3 *db, char **pzErrMsg,
                          const sqlite3_api_routines *pApi) {
  return sqlite3_dateutilparse_init(db, pzErrMsg, pApi ? pApi : &fallback_api);
}
