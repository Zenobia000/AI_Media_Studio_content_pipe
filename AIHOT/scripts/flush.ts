// Ends the process after what was written to stdout has gone out. A comparison script that calls process.exit() right after console.log
// loses the end of its output when stdout is a pipe on Linux (cut at 64 KiB), so a test reading it sees half a JSON document.
export function exitAfterFlush(code = 0): Promise<never> {
  return new Promise<never>(() => {
    process.stdout.write("", () => process.exit(code));
  });
}
