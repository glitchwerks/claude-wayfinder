// SessionEnd hook: delete the PID-keyed session state file on clean exit.
//
// Delegates to the plugin's Python venv to remove the file written by
// session-start-record-session.js at:
//
//   ~/.claude/state/wayfinder-sessions/<ppid>-<create_time_int>.txt
//
// Best-effort: missing file is silently ignored.  Any error is logged to
// stderr and the hook exits 0 — it must never block CC shutdown.
//
// See hooks/session-end-cleanup-session.py and issue #296 for design details.
//
// Environment overrides (for testing):
//   CLAUDE_WAYFINDER_SESSION_HOOK_PYTHON — override the Python interpreter
//                                          (bypasses setup-state lookup)

const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

const SCRIPT = path.join(__dirname, "session-end-cleanup-session.py");

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
      // Setup not complete — skip silently.
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
        `[session-end-cleanup-session] error: ${detail}\n`
      );
    }

    process.exit(0);
  });
})();
