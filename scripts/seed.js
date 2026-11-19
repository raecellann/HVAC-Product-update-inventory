// Thin JS wrapper around scripts/seed.py so the user can run `npm run seed`.
const { spawn } = require("node:child_process");
const path = require("node:path");
const { findPython } = require("./pyexec");

const DATA_DIR = process.env.DATA_DIR || path.resolve(__dirname, "..", "dev-data");

const env = {
  ...process.env,
  ACCESS_KEY: process.env.ACCESS_KEY || "dev-key",
  DATA_DIR,
  SHOPIFY_STORE_DOMAIN: process.env.SHOPIFY_STORE_DOMAIN || "fake.myshopify.com",
  SHOPIFY_ACCESS_TOKEN: process.env.SHOPIFY_ACCESS_TOKEN || "shpat_FAKE",
  SHOPIFY_LOCATION_ID:  process.env.SHOPIFY_LOCATION_ID  || "gid://shopify/Location/1",
  PYTHONUNBUFFERED: "1",
};

const proc = spawn(
  findPython(),
  [path.resolve(__dirname, "seed.py")],
  { env, stdio: "inherit", cwd: path.resolve(__dirname, "..") }
);

proc.on("exit", (code) => process.exit(code ?? 0));
