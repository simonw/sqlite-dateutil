/*
** Runs fuzz_parse.c on files given on the command line, without libFuzzer.
** Use it to reproduce a crash file from CI with any compiler that has ASan
** (including Apple clang, which doesn't ship libFuzzer):
**
**   python tools/build.py --fuzz-replay
**   build/fuzz_replay crash-1234abcd
*/
#include "fuzz_parse.c"

int main(int argc, char **argv){
  int i;
  for(i = 1; i < argc; i++){
    FILE *f = fopen(argv[i], "rb");
    long n;
    uint8_t *buf;
    if( f == 0 ){ perror(argv[i]); return 1; }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf = (uint8_t*)malloc(n > 0 ? (size_t)n : 1);
    if( buf == 0 || fread(buf, 1, (size_t)n, f) != (size_t)n ){
      fprintf(stderr, "%s: read failed\n", argv[i]);
      return 1;
    }
    fclose(f);
    LLVMFuzzerTestOneInput(buf, (size_t)n);
    free(buf);
  }
  printf("ran %d input(s)\n", argc - 1);
  return 0;
}
