// Shared hook utility: parse JSON from Claude Code's hook stdin.
//
// Claude Code may produce literal (unescaped) newlines inside JSON string
// values when a command contains a bash heredoc or multi-line --body argument.
// Standard JSON.parse rejects these. This function retries by scanning the raw
// text character-by-character, tracking string context, and escaping any bare
// control characters it finds inside string values before re-parsing.
//
// Usage:
//   const parseInput = require('./parse-input');
//   const input = parseInput(rawStdinString);  // throws only if truly unparseable

module.exports = function parseInput(raw) {
  // Fast path: standard parse succeeds (the common case).
  try {
    return JSON.parse(raw);
  } catch (_) {}

  // Slow path: escape bare control characters inside JSON string values.
  let inString = false;
  let escaped = false;
  let result = "";

  for (const ch of raw) {
    if (escaped) {
      // Previous char was a backslash — this char is always literal.
      result += ch;
      escaped = false;
      continue;
    }
    if (ch === "\\" && inString) {
      // Start of an escape sequence inside a string.
      result += ch;
      escaped = true;
      continue;
    }
    if (ch === '"') {
      // Toggle string context.
      inString = !inString;
      result += ch;
      continue;
    }
    if (inString) {
      // Inside a string: control characters must be escaped.
      if (ch === "\n") {
        result += "\\n";
        continue;
      }
      if (ch === "\r") {
        result += "\\r";
        continue;
      }
      if (ch === "\t") {
        result += "\\t";
        continue;
      }
    }
    result += ch;
  }

  // This will throw if the input is genuinely malformed (not just unescaped newlines).
  return JSON.parse(result);
};
