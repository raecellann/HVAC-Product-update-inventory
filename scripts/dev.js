// Orchestrator: spawns FastAPI (dev-api.js) and Vite, prefixes their output,
// and prints the one URL to open. Tears down both on Ctrl+C.
const { spawn, spawnSync } = require("node:child_process");
const path = require("node:path");

const DEV_URL = "http://localhost:5173/";
const ROOT = path.resolve(__dirname, "..");

function freeStalePorts() {
  if (process.platform !== "win32") return;
  // Kill anything holding our dev ports from a previous crashed session,
  // plus orphan uvicorn multiprocessing children.
  const ps = [
    "Get-NetTCPConnection -LocalPort 5173,8001 -ErrorAction SilentlyContinue | ",
    "  ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop } catch {} };",
    "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'webapp|uvicorn' } | ",
    "  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }",
  ].join("");
  spawnSync("powershell", ["-NoProfile", "-Command", ps], { stdio: "ignore" });
}

freeStalePorts();

function banner() {
  const line = "═".repeat(60);
  const msg = [
    "",
    line,
    "  HVAC Inventory — dev server",
    `  Open: ${DEV_URL}`,
    line,
    "",
  ].join("\n");
  process.stdout.write(msg);
}

function prefix(stream, tag, color) {
  let buf = "";
  stream.on("data", (chunk) => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop();
    for (const line of lines) {
      process.stdout.write(`\x1b[${color}m[${tag}]\x1b[0m ${line}\n`);
    }
  });
}

function startProcess(name, color, cmd, args) {
  const proc = spawn(cmd, args, { cwd: ROOT, shell: process.platform === "win32" });
  prefix(proc.stdout, name, color);
  prefix(proc.stderr, name, color);
  return proc;
}

banner();

const api = startProcess("API", 36, "node", ["scripts/dev-api.js"]);
const web = startProcess("WEB", 35, "npx", ["vite"]);

let stopping = false;
function killTree(p) {
  if (!p || p.killed) return;
  if (process.platform === "win32") {
    // Uvicorn's --reload parent spawns a child process — SIGTERM doesn't
    // propagate on Windows, so use taskkill /T to wipe the whole tree.
    try {
      spawn("taskkill", ["/pid", String(p.pid), "/f", "/t"], { stdio: "ignore" });
    } catch {}
  } else {
    try { p.kill("SIGTERM"); } catch {}
  }
}
function stop() {
  if (stopping) return;
  stopping = true;
  killTree(api);
  killTree(web);
  setTimeout(() => process.exit(0), 800).unref();
}

process.on("SIGINT",  stop);
process.on("SIGTERM", stop);

api.on("exit", (code) => { console.log(`[API] exited (${code})`); stop(); });
web.on("exit", (code) => {
  console.log(`[WEB] exited (${code})`);
  if (code === 1) {
    console.log("");
    console.log("  Vite couldn't bind port 5173. Something else is holding it.");
    console.log("  Run in PowerShell:");
    console.log("    Get-NetTCPConnection -LocalPort 5173,8001 -ErrorAction SilentlyContinue |");
    console.log("      ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }");
    console.log("");
  }
  stop();
});
