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
.\run_daily.bat
```
Or double-click `run_daily.bat` in File Explorer.

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
├── input/                  📥 Drop daily Excel files here
├── archive/                📦 Processed files (timestamped)
├── logs/                   📋 Per-run logs + unmatched CSV reports
├── .env                    🔐 Credentials (gitignored)
├── sku_cache.json          🗂️ SKU → inventoryItemId map (gitignored)
├── run_daily.bat           ▶️ Main entry point
├── refresh_sku_cache.bat   🔄 Weekly cache refresh
├── update_inventory.py     Main script
├── build_sku_cache.py      Cache builder (bulk query)
├── find_location.py        Lists Shopify locations
├── get_access_token.py     OAuth token helper (one-time use)
├── shopify_client.py       GraphQL API client w/ throttle handling
└── config.py               Config loader
```

---

## 🔄 Weekly Maintenance

Refresh the SKU cache (needed when new products are added):
```powershell
.\refresh_sku_cache.bat
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
To find valid IDs: `python find_location.py`

### `No Excel file found in input/`
Drop an `.xlsx` file in `input/` before running.

### `SKU column 'SKU' not found. Header row: ...`
Excel column names don't match. Update `EXCEL_SKU_COLUMN=` in `.env`.

### Many unmatched SKUs
1. **Cache is stale** → `.\refresh_sku_cache.bat`
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
   - Program: `<full path to>\run_daily.bat`
   - Start in: `<full path to project folder>`
4. Finish

Recommended: a second task for weekly cache refresh (Sunday 11 PM, pointing at
`refresh_sku_cache.bat`).

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
- `SHOPIFY_LOCATION_ID` — obtained via `python find_location.py`

### 4. Getting the Access Token

The Dev Dashboard doesn't expose offline tokens directly. Use the OAuth
authorize URL → extract `code` → exchange for `shpat_` token:

1. Open in browser (replace `YOUR_STORE` and `YOUR_CLIENT_ID`):
   ```
   https://YOUR_STORE.myshopify.com/admin/oauth/authorize?client_id=YOUR_CLIENT_ID&scope=write_inventory,read_inventory,read_locations,read_products&redirect_uri=https://localhost/auth/callback&state=xyz
   ```
2. Approve the install. You'll land on a `localhost refused to connect` page — expected.
3. Copy the full URL from the browser address bar (contains `?code=...`).
4. Run `python get_access_token.py` and paste when prompted.
5. Save the printed `SHOPIFY_ACCESS_TOKEN=...` line into `.env`.

### 5. Build the SKU cache (one-time, ~1 min per 70k SKUs)

```powershell
python build_sku_cache.py
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

## License

MIT. Use at your own risk — test with `DRY_RUN=true` before production.
