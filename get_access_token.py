"""One-time helper: exchange a Shopify OAuth 'code' for a permanent
Admin API access token (shpat_...).

Usage:
  1. Reinstall the app on your store (triggers the OAuth redirect).
  2. When Chrome shows the localhost error page, copy the FULL URL from
     the browser address bar (it contains ?code=... &shop=... etc.).
  3. Run this script and paste the URL + Client ID + Client Secret when
     prompted.
  4. The script prints the SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN
     lines ready to paste into .env.

The access token Shopify returns here is PERMANENT (offline access) and
does not expire unless the app is uninstalled or scopes change.
"""
from __future__ import annotations

import sys
from getpass import getpass
from urllib.parse import parse_qs, urlparse

import requests


def main() -> int:
    print("=" * 60)
    print("Shopify OAuth code -> access token exchange")
    print("=" * 60)
    print()
    print("STEP 1: In your Dev Dashboard, click 'Overview' or the install")
    print("        link to reinstall the app on HVAC MORE.")
    print("STEP 2: When Chrome shows the localhost error page, copy the")
    print("        FULL URL from the address bar (not just the error).")
    print("        It looks like:")
    print("        localhost/?code=xxx&hmac=yyy&shop=zzz.myshopify.com&...")
    print()

    url = input("Paste the full localhost URL: ").strip()
    if not url:
        print("ERROR: no URL provided")
        return 1

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    code = (qs.get("code") or [None])[0]
    shop = (qs.get("shop") or [None])[0]

    if not code:
        print("ERROR: no 'code' parameter in the URL. Reinstall and try again.")
        print("       The URL must include ?code=...")
        return 1
    if not shop:
        print("ERROR: no 'shop' parameter in the URL. Reinstall and try again.")
        return 1

    print(f"\n  shop = {shop}")
    print(f"  code = {code[:12]}... (captured)\n")

    client_id = input("Paste the Client ID from Dev Dashboard Settings: ").strip()
    client_secret = getpass("Paste the Client Secret (hidden input): ").strip()

    if not client_id or not client_secret:
        print("ERROR: client_id and client_secret are required")
        return 1

    print("\nExchanging code for access token...")
    resp = requests.post(
        f"https://{shop}/admin/oauth/access_token",
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"ERROR: Shopify returned HTTP {resp.status_code}")
        print(resp.text)
        print()
        print("Common causes:")
        print("  - Code expired (they last ~10 min). Reinstall for a fresh one.")
        print("  - Wrong client_secret. Re-copy from Dev Dashboard Settings.")
        print("  - Wrong shop domain in the URL.")
        return 1

    data = resp.json()
    token = data.get("access_token")
    scope = data.get("scope", "")

    if not token:
        print("ERROR: Shopify response had no access_token:")
        print(data)
        return 1

    print()
    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print(f"Granted scopes: {scope}")
    print()
    print("Paste these two lines into your .env file:")
    print()
    print(f"SHOPIFY_STORE_DOMAIN={shop}")
    print(f"SHOPIFY_ACCESS_TOKEN={token}")
    print()
    print("(The token starts with 'shpat_' and does not expire.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
