// SessionStart hook: record session_id in a PID-keyed state file.
//
// Reads the session_id from stdin JSON (standard CC hook payload) and
// delegates to the plugin's Python venv to write:
//
//   ~/.claude/state/wayfinder-sessions/<ppid>-<create_time_int>.txt
//
// using psutil to capture the CC process's start time, making the key
// unique across PID reuse.  See hooks/session-start-record-session.py
// and the design rationale in issue #296.
//
// The hook is silent on success.  On any error it logs to stderr and
// exits 0 — it must never block a CC session start.
//
// Environment overrides (for testing):
//   CLAUDE_WAYFINDER_SESSION_HOOK_PYTHON — override the Python interpreter
//                                          (bypasses setup-state lookup)

const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

const SCRIPT = path.join(__dirname, "session-start-record-session.py");

function resolvePython(setupState) {
  if (process.env.CLAUDE_WAYFINDER_SESSION_HOOK_PYTHON) {
    return process.env.CLAUDE_WAYFINDER_SESSION_HOOK_PYTHON;
  }
  if (setupState && setupState.status === "VALID" && setupState.flag) {
    return getVenvPython(setupState.flag.venv_path);
  }
  return null;
}

(function main() {
  let data = "";
  process.stdin.resume();
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => (data += chunk));
  process.stdin.on("end", () => {
    const setupState = readSetupState(getCurrentVersion());
    const python = resolvePython(setupState);

    if (!python) {
      // Setup not complete — skip silently (catalog health hook will
      // emit the setup-required banner; no need to duplicate it here).
      process.exit(0);
    }

    const result = spawnSync(python, [SCRIPT], {
      input: data,
      encoding: "utf8",
      timeout: 5000,
    });

    if (result.error || result.status !== 0) {
      const detail = result.error
        ? result.error.message
        : (result.stderr || "").trim();
      process.stderr.write(
        `[session-start-record-session] error: ${detail}\n`
      );
    }

    process.exit(0);
  });
})();
