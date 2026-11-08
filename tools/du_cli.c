/*
** Standalone driver for the parser core, without SQLite. Used by the test
** suite to run the parser under AddressSanitizer/UBSan and with a fixed
** "today" date.
**
**   du_cli YEAR MONTH DAY CURRENT_YEAR < records
**
** Each record on stdin is: 1 flag byte, 4-byte little-endian length,
** then that many bytes of input. For each record, one line is written to
** stdout: the ISO result, or "NULL".
*/
#define DATEUTIL_NO_SQLITE 1
#include "../dateutil_parse.c"

int main(int argc, char **argv){
  DuToday td;
  unsigned char hdr[5];
  if( argc != 5 ){
    fprintf(stderr, "usage: %s YEAR MONTH DAY CURRENT_YEAR < records\n", argv[0]);
    return 2;
  }
  td.year = atoi(argv[1]);
  td.month = atoi(argv[2]);
  td.day = atoi(argv[3]);
  td.cur_year = atoi(argv[4]);
  while( fread(hdr, 1, 5, stdin) == 5 ){
    size_t n = (size_t)hdr[1] | ((size_t)hdr[2] << 8)
             | ((size_t)hdr[3] << 16) | ((size_t)hdr[4] << 24);
    char *buf = (char*)malloc(n ? n : 1);
    char out[64];
    int rc;
    if( buf == 0 ) return 1;
    if( fread(buf, 1, n, stdin) != n ){ free(buf); return 1; }
    rc = du_parse(buf, (int)n, hdr[0], &td, out, sizeof(out));
    free(buf);
    if( rc > 0 ) printf("%s\n", out);
    else printf("NULL\n");
  }
  return 0;
}
