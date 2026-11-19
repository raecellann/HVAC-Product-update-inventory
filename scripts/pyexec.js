// Locates the project's Python interpreter: .venv first, then system python.
const fs = require("node:fs");
const path = require("node:path");

function findPython() {
  const root = path.resolve(__dirname, "..");
  const candidates = process.platform === "win32"
    ? [path.join(root, ".venv", "Scripts", "python.exe")]
    : [path.join(root, ".venv", "bin", "python"), path.join(root, ".venv", "bin", "python3")];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return process.platform === "win32" ? "python" : "python3";
}

module.exports = { findPython };
