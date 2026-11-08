/*
** libFuzzer harness for the parser core (no SQLite needed).
**
** Input layout:  [flags][year lo][year hi][month][day][cur_year lo][cur_year hi] text...
**
** The first 7 bytes pick the flags (fuzzy/dayfirst) and the "today" date
** that fills in missing fields, so month-end clamping, leap years, weekday
** arithmetic and two-digit year conversion are all fuzzed, not just the
** text. Everything after that is the string to parse.
**
** Besides crashes, ASan and UBSan, it checks these oracles:
**   - the result is a well-formed ISO 8601 string that fits its buffer
**   - parsing is deterministic
**   - the result parses back to itself (strict and fuzzy modes)
**
** Build:  python tools/build.py --fuzz      (Linux clang, or Homebrew LLVM)
** Run:    see fuzz/README.md
*/
#define DATEUTIL_NO_SQLITE 1
#include "../dateutil_parse.c"

#define HDR 7

static int dim(int y, int m){
  static const int d[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  if( m == 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) ) return 29;
  return d[m-1];
}

static int isdig(char c){ return c >= '0' && c <= '9'; }

/* YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM[:SS[.ffffff]]] */
static int well_formed(const char *s){
  static const char pat[] = "dddd-dd-ddTdd:dd:dd";
  size_t i, n = strlen(s);
  if( n < sizeof(pat) - 1 ) return 0;
  for(i = 0; i < sizeof(pat) - 1; i++){
    if( pat[i] == 'd' ? !isdig(s[i]) : s[i] != pat[i] ) return 0;
  }
  for(; i < n; i++){
    char c = s[i];
    if( !(isdig(c) || c == '.' || c == ':' || c == '+' || c == '-') ) return 0;
  }
  return 1;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size){
  DuToday td;
  char out[64], again[64];
  const char *text;
  int n, flags, rc, rc2, f;

  if( size < HDR ) return 0;
  flags = data[0] & (DU_FUZZY | DU_DAYFIRST);
  td.year = 1 + (int)(((unsigned)data[1] | ((unsigned)data[2] << 8)) % 9999);
  td.month = 1 + data[3] % 12;
  td.day = 1 + data[4] % dim(td.year, td.month);
  td.cur_year = 1 + (int)(((unsigned)data[5] | ((unsigned)data[6] << 8)) % 9999);
  text = (const char*)data + HDR;
  n = (int)(size - HDR);

  memset(out, 0x55, sizeof(out));
  rc = du_parse(text, n, flags, &td, out, sizeof(out));
  if( rc < 0 ) return 0;                         /* out of memory */
  if( rc == 0 ) return 0;                        /* unparseable: NULL */

  if( memchr(out, 0, sizeof(out)) == 0 ) __builtin_trap();   /* unterminated */
  if( !well_formed(out) ) __builtin_trap();

  rc2 = du_parse(text, n, flags, &td, again, sizeof(again));
  if( rc2 != rc || strcmp(out, again) != 0 ) __builtin_trap();   /* nondeterministic */

  /* Not with DU_DAYFIRST: dateutil itself reads '2003-10-09' as 10 Sep
  ** when dayfirst=True, and this port matches it. */
  for(f = 0; f <= DU_FUZZY; f += DU_FUZZY){
    rc2 = du_parse(out, (int)strlen(out), f, &td, again, sizeof(again));
    if( rc2 != 1 || strcmp(out, again) != 0 ) __builtin_trap();  /* no round trip */
  }
  return 0;
}
