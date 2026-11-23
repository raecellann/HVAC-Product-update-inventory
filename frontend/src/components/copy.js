// One source of truth for user-facing labels. Importing from here keeps the
// four preview variants from drifting on wording and plural forms.

export const KIND_LABEL = {
  preview:         "Inventory preview",
  apply:           "Inventory updated",
  cache_refresh:   "Product list refresh",
  preview_prices:  "Price preview",
  apply_prices:    "Prices updated",
  preview_both:    "Inventory + price preview",
  apply_both:      "Inventory + prices updated",
};

export const STATUS_LABEL = {
  queued:      "Queued",
  running:     "Running",
  succeeded:   "Succeeded",
  failed:      "Failed",
  interrupted: "Interrupted",
};

// Problem-row categories → plain-language group label + description used in
// the ProblemsTable. Keep these short — the per-row `reason` already has
// the detail.
export const PROBLEM_KIND = {
  unmatched:     { label: "Not in Shopify", short: "No match" },
  bad_qty:       { label: "Bad quantity",   short: "Bad qty" },
  bad_price:     { label: "Bad price",      short: "Bad price" },
  duplicate_sku: { label: "Duplicate SKU",  short: "Duplicate" },
  untracked:     { label: "Not tracked",    short: "Not tracked" },
};

// Filter-chip labels. Rendered in the order of the keys.
export const INVENTORY_FILTERS = [
  { id: "all",            label: "All matched" },
  { id: "changing",       label: "Changing" },
  { id: "out_of_stock",   label: "Going out of stock" },
  { id: "back_in_stock",  label: "Back in stock" },
  { id: "no_change",      label: "No change" },
];

export const PRICE_FILTERS = [
  { id: "all",             label: "All matched" },
  { id: "price_up",        label: "Price up" },
  { id: "price_down",      label: "Price down" },
  { id: "price_no_change", label: "No change" },
];

export const PER_PAGE_OPTIONS = [50, 100, 250];

// Column-missing warning → the plain-language message + the mode the user
// would switch to if they click the one-click fallback. Keyed by the flag
// string the backend puts in run.warnings.
export const MISSING_COLUMN_PROMPTS = {
  missing_price_column: {
    title: "This file has no price column.",
    body:  "Your file is missing the price column, so prices can't be previewed.",
    switchLabel: "Switch to Inventory only",
    switchMode:  "inventory",
  },
  missing_qty_column: {
    title: "This file has no quantity column.",
    body:  "Your file is missing the quantity column, so inventory can't be previewed.",
    switchLabel: "Switch to Prices only",
    switchMode:  "prices",
  },
  missing_sku_column: {
    title: "This file has no SKU column.",
    body:  "Nothing can be matched without a SKU column. Please check the file and upload again.",
    switchLabel: null,
    switchMode:  null,
  },
};

export const CONFIRM_COPY = {
  inventory: {
    title: "Update Shopify stock",
    intro: "This will change live quantities on your Shopify store.",
    cta:   "Update Shopify stock",
    cancel:"Cancel",
  },
  prices: {
    title: "Update Shopify prices",
    intro: "This will change live prices on your Shopify store.",
    cta:   "Update Shopify prices",
    cancel:"Cancel",
  },
  both: {
    title: "Update Shopify inventory and prices",
    intro: "This will change live quantities and prices on your Shopify store.",
    cta:   "Update Shopify",
    cancel:"Cancel",
  },
};
