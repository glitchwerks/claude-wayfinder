#!/usr/bin/env node
// Fake import-probe shim: always exits 0 (simulates successful `import claude_wayfinder`).
// Used via CLAUDE_WAYFINDER_PROBE_CMD in check-catalog-health tests.
process.exit(0);
