# Shopify Inventory Updater

Daily stock sync from an Excel file → Shopify Admin API.
Built for large catalogs (tested with ~70,000 SKUs).

Folder-based workflow: drop today's Excel file into `input/`, run a batch
script, and the tool batches `inventorySetQuantities` GraphQL mutations
against Shopify with automatic throttle handling. No web server, no OAuth
dance at runtime — one-time setup, then fully automatic via Task Scheduler.

---

## Features

- **Scales to 70k+ SKUs** — uses Shopify's Bulk Operations API for the one-time
  cache build; batched GraphQL mutations for daily updates
- **Rate-limit aware** — exponential backoff on `THROTTLED`, proactive pacing
  when the cost bucket runs low
- **SKU → inventoryItemId cache** — avoids re-querying variants every day
- **Safety-first defaults**: `DRY_RUN=true` lets you preview a run without
  touching Shopify; `absolute` quantity mode is idempotent (rerunning the same
  file produces the same result)
- **Problem-SKU reporting** — unmatched SKUs, bad quantities, duplicate SKUs,
  untracked variants, and Excel↔Shopify title mismatches all logged to CSVs
- **One-location-at-a-time** by design — safer default for multi-location
  stores; easily extended to multi-location if needed

---

## 🚀 Daily Workflow

### Step 1: Drop the Excel file
Save the daily file into the `input/` folder in the project directory.
Any filename works. The script auto-picks the **most recent** `.xlsx` file.

### Step 2: Run the update
From PowerShell in the project folder:
```powershell
.\tools\run_daily.bat
```
Or double-click `tools\run_daily.bat` in File Explorer.

### Step 3: Check the results
Open the newest file in `logs/`:
```
logs\run_<timestamp>.log
```
Look for the summary line:
```
Matched=X | Unmatched=Y | Bad qty=Z | Duplicate SKUs=... | Untracked=... | Title mismatches=...
```
Processed Excel files are moved to `archive/` with a timestamp.

---

## 🧪 Dry Run vs. Live Run

Toggle in `.env`:

| Setting | What happens |
|---|---|
| `DRY_RUN=true` | Reads Excel, matches SKUs, writes logs — **does NOT push to Shopify** |
| `DRY_RUN=false` | Reads Excel, matches SKUs, **updates Shopify inventory** |

**Always dry-run first** when:
- Trying a new Excel file format
- After adding new product lines in Shopify
- After any config changes

---

## 📁 Folder Map

```
.
├── input/                      📥 Drop daily Excel files here
├── archive/                    📦 Processed files (timestamped)
├── logs/                       📋 Per-run logs + unmatched CSV reports
├── .env                        🔐 Credentials (gitignored)
├── sku_cache.json              🗂️ SKU → inventoryItemId map (gitignored)
├── core/                       Python library modules
│   ├── config.py               Config loader
│   ├── shopify_client.py       GraphQL API client w/ throttle handling
│   ├── build_sku_cache.py      Cache builder (bulk query)
│   ├── compare_inventory.py    Read-only diff vs Shopify
│   ├── update_inventory.py     Main inventory update
│   └── update_prices.py        Price update
├── tools/                      Entry points + one-off helpers
│   ├── run_daily.bat           ▶️ Main entry point
│   ├── refresh_sku_cache.bat   🔄 Weekly cache refresh
│   ├── run_web.py              Local web UI launcher
│   ├── update_prices_cli.py    Terminal price-push CLI
│   ├── find_location.py        Lists Shopify locations
│   └── get_access_token.py     OAuth token helper (one-time use)
├── webapp/                     FastAPI web app
└── frontend/                   Vite-built HTML/CSS/JS pages
```

---

## 🔄 Weekly Maintenance

Refresh the SKU cache (needed when new products are added):
```powershell
.\tools\refresh_sku_cache.bat
```
Takes ~1 minute per 70k SKUs. Must run after:
- Adding new products in Shopify
- Fixing duplicate SKUs
- Changing SKU values on existing variants

---

## ⚙️ Expected Excel Format

The script looks for these columns (case-insensitive) in the first sheet:

| Column | Required? | Purpose |
|---|---|---|
| `SKU` | ✅ Yes | Matches against Shopify variant SKU |
| `Quantity` | ✅ Yes | Stock level to set (absolute number) |
| `Title` | ⚠️ Optional | Logged only, for mismatch detection |

Override column names via `.env`:
```
EXCEL_SKU_COLUMN=SKU
EXCEL_QTY_COLUMN=Quantity
EXCEL_TITLE_COLUMN=Title
```

---

## 🧰 Troubleshooting

### `SHOPIFY_LOCATION_ID is not set`
Open `.env`, set it to a real Location GID:
```
SHOPIFY_LOCATION_ID=gid://shopify/Location/1234567890
```
To find valid IDs: `python tools/find_location.py`

### `No Excel file found in input/`
Drop an `.xlsx` file in `input/` before running.

### `SKU column 'SKU' not found. Header row: ...`
Excel column names don't match. Update `EXCEL_SKU_COLUMN=` in `.env`.

### Many unmatched SKUs
1. **Cache is stale** → `.\tools\refresh_sku_cache.bat`
2. **SKU format mismatch** → check `logs/unmatched_<timestamp>.csv`
3. **Wrong column** → verify Excel columns match `.env`

### Many `duplicate_sku` errors
SKUs assigned to multiple variants. Fix them in Shopify admin (each SKU must
be unique), then rebuild the cache.

### Many `untracked` entries
"Track quantity" is OFF for those variants. Turn it on in Shopify admin.

### `THROTTLED` / HTTP 429
Rate-limit hit. Script auto-retries with backoff. If it fails after 6 attempts,
just rerun.

### Access token no longer works
Re-get it via the OAuth flow (see **Getting the Access Token** below).

---

## 🕒 Scheduling (Windows Task Scheduler)

1. **Task Scheduler** → **Create Basic Task** → Name: `Shopify Inventory Daily`
2. Trigger: Daily at your chosen time
3. Action: Start a program
   - Program: `<full path to>\tools\run_daily.bat`
   - Start in: `<full path to project folder>`
4. Finish

Recommended: a second task for weekly cache refresh (Sunday 11 PM, pointing at
`tools\refresh_sku_cache.bat`).

---

## 🏗️ Initial Setup

### 1. Create a Shopify app (one-time)

In Shopify's **Dev Dashboard** (https://dev.shopify.com):
1. Create an app → name it anything
2. Set redirect URL: `https://localhost/auth/callback`
3. Grant scopes: `read_products`, `read_inventory`, `write_inventory`, `read_locations`
4. Install on your store

### 2. Install Python environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure `.env`

```powershell
copy .env.example .env
notepad .env
```

Fill in:
- `SHOPIFY_STORE_DOMAIN` — your `*.myshopify.com` domain
- `SHOPIFY_ACCESS_TOKEN` — obtained via OAuth (see below)
- `SHOPIFY_LOCATION_ID` — obtained via `python tools/find_location.py`

### 4. Getting the Access Token

The Dev Dashboard doesn't expose offline tokens directly. Use the OAuth
authorize URL → extract `code` → exchange for `shpat_` token:

1. Open in browser (replace `YOUR_STORE` and `YOUR_CLIENT_ID`):
   ```
   https://YOUR_STORE.myshopify.com/admin/oauth/authorize?client_id=YOUR_CLIENT_ID&scope=write_inventory,read_inventory,read_locations,read_products&redirect_uri=https://localhost/auth/callback&state=xyz
   ```
2. Approve the install. You'll land on a `localhost refused to connect` page — expected.
3. Copy the full URL from the browser address bar (contains `?code=...`).
4. Run `python tools/get_access_token.py` and paste when prompted.
5. Save the printed `SHOPIFY_ACCESS_TOKEN=...` line into `.env`.

### 5. Build the SKU cache (one-time, ~1 min per 70k SKUs)

```powershell
python -m core.build_sku_cache
```

---

## 📊 What Gets Logged

Each run writes to `logs/`:

| File | Contents |
|---|---|
| `run_<timestamp>.log` | Main execution log — info, warnings, errors |
| `unmatched_<timestamp>.csv` | SKUs that couldn't be matched + categories |
| `title_mismatches_<timestamp>.csv` | Excel title ≠ Shopify title |
| `user_errors_<timestamp>.json` | Shopify API errors (live runs only) |

---

## 🔒 Security

- `.env` holds a Shopify access token with write-inventory scope — treat it
  like a password
- `.gitignore` excludes `.env`, `sku_cache.json`, `input/`, `archive/`, `logs/`
- If the token leaks: rotate the Client Secret in Dev Dashboard, then re-run
  the OAuth flow to get a fresh token

---

## 🌐 Web App (for non-technical users)

The same inventory sync is available as a small web app so the client can run
it from a browser. The CLI workflow above is unchanged — the web app reuses
the same Python modules under the hood.

Architecture:
- **Backend**: FastAPI, serves only JSON + static files.
- **Frontend**: plain HTML / CSS / vanilla JS, built with Vite. No React.
  One HTML file per page: `index`, `preview`, `history`, `run`.
- **One Render service**: Dockerfile builds the frontend with Node, then
  runs FastAPI with Python. Both are protected by the same access-key check.

### What the client does

1. Opens the site.
2. Clicks **Refresh product list** once (and thereafter whenever Shopify
   products are added — the app warns if it's been more than 7 days).
3. Uploads today's `.xlsx` file.
4. Reviews the preview: counts, warnings, and a searchable before/after table.
5. Clicks **Update Shopify** to apply the changes.
6. Can see any run on **Latest changes** for 90 days.

### Local dev

Requirements: Python 3.11+ and Node 20+.

```powershell
# One-time
pip install -r requirements.txt
npm install

# Seed fake data so the UI works without Shopify:
npm run seed

# Start Vite (hot reload) and FastAPI together:
npm run dev
```

The command prints the exact URL to open, including the dev access key.
Vite runs on `127.0.0.1:5173` and proxies `/api`, `/download`, `/healthz`
through to FastAPI on `127.0.0.1:8001`.

In dev:
- `ACCESS_KEY` defaults to `dev-key` if unset. Never on Render.
- `DISABLE_APPLY=true`. The "Update Shopify" button still shows; the server
  refuses with "Apply is disabled in dev mode". Lets you exercise the UI
  without risk.
- Data lives in `./dev-data/` (gitignored).

### Environment

| Var | Purpose |
|---|---|
| `DATA_DIR`     | Where uploads, run history, and `sku_cache.json` live. Defaults to the project folder for the CLI; on Render set it to `/var/data`. |
| `DISABLE_APPLY`| `true` to make the apply endpoint refuse. Set automatically by `npm run dev`. |

The CLI's existing `SHOPIFY_*` vars still apply.

### Production build

```powershell
npm run build   # → frontend/dist
```

FastAPI serves everything from `frontend/dist` when it exists.

### Deploy to Render

The repo ships a Docker blueprint (`render.yaml` + `Dockerfile`):

1. On Render, create a **Blueprint** service pointing at this repo.
2. In the dashboard, set the three Shopify secrets: `SHOPIFY_STORE_DOMAIN`,
   `SHOPIFY_ACCESS_TOKEN`, `SHOPIFY_LOCATION_ID`.
3. Deploy. The multi-stage `Dockerfile` builds the frontend with Node, then
   copies `frontend/dist` into the Python image. A 1 GB persistent disk is
   mounted at `/var/data`.

> **Why Docker, not the native Python runtime?** Render's native Python
> runtime does not include Node, and installing it in `buildCommand` is
> fragile (no root). Docker handles both languages cleanly.

### Safety behaviors

- Preview never writes to Shopify. Apply always runs in absolute-quantity
  mode regardless of `.env`, so re-uploading the same file is idempotent.
- Only one job (preview / apply / cache refresh) runs at a time — anything
  else gets a 409.
- A preview can only be applied **once, within 24 hours**. After that the
  Shopify quantities in the preview are considered stale and the user is
  asked to re-upload.
- If the server is restarted mid-job, that run is marked `interrupted` on
  next boot.
- Run-summary rows are kept forever; the large per-run detail files
  (uploaded Excel + preview JSON) are auto-pruned after 90 days.

---

## License

MIT. Use at your own risk — test with `DRY_RUN=true` before production.
