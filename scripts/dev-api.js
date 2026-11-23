// Starts the FastAPI backend in dev mode: fixed ACCESS_KEY if unset,
// data dir under ./dev-data, uvicorn --reload on port 8001.
const { spawn } = require("node:child_process");
const path = require("node:path");
const { findPython } = require("./pyexec");

const DATA_DIR = process.env.DATA_DIR || path.resolve(__dirname, "..", "dev-data");

// Intentionally do NOT set ACCESS_KEY in dev — the middleware treats an
// unset key as "auth disabled" so you can just open the page. Production
// (Dockerfile + render.yaml) sets ACCESS_KEY explicitly.
//
// Pushes to Shopify are enabled by default on localhost so the user can
// apply changes without switching to the deployed site. Set
// `DISABLE_APPLY=true` before `npm run dev` to flip back to read-only.
const env = {
  ...process.env,
  DATA_DIR,
  DISABLE_APPLY: process.env.DISABLE_APPLY || "false",
  PYTHONUNBUFFERED: "1",
};

// Note: --reload is intentionally OFF. Uvicorn's reload spawns a child via
// multiprocessing; on Windows those children orphan when the parent is
// killed, leaving port 8001 stuck. Restart `npm run dev` after backend edits.
const proc = spawn(
  findPython(),
  [
    "-m", "uvicorn", "webapp.app:app",
    "--host", "127.0.0.1",
    "--port", "8001",
  ],
  { env, stdio: "inherit", shell: false, cwd: path.resolve(__dirname, "..") }
);

proc.on("exit", (code) => process.exit(code ?? 0));
