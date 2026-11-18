"""List all inventory locations so you can pick which one to update.

Run once. Copy the location GID into SHOPIFY_LOCATION_ID in .env.
"""
from __future__ import annotations

from config import load_config
from shopify_client import ShopifyClient

QUERY = """
{
  locations(first: 50) {
    edges {
      node {
        id
        name
        isActive
        address { city country }
      }
    }
  }
}
"""


def main() -> None:
    cfg = load_config()
    client = ShopifyClient(cfg)
    data = client.graphql(QUERY)
    edges = data["locations"]["edges"]
    print(f"\nFound {len(edges)} location(s):\n")
    for e in edges:
        n = e["node"]
        addr = n.get("address") or {}
        print(f"  {n['id']}")
        print(f"    name   : {n['name']}")
        print(f"    active : {n['isActive']}")
        print(f"    where  : {addr.get('city') or '-'}, {addr.get('country') or '-'}")
        print()
    print("Copy the `gid://shopify/Location/...` line of the location you want")
    print("to update, and paste it as SHOPIFY_LOCATION_ID in your .env file.\n")


if __name__ == "__main__":
    main()
