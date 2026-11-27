"""Read-only: dump shipping settings from Shopify. Split into cheap queries
to stay under the 1000-cost single-query limit."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import PROJECT_ROOT, load_config
from core.shopify_client import ShopifyClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit_shipping")

OUT = PROJECT_ROOT / "pricing_audit_shipping_2026-11-23.json"


PROFILES_QUERY = """
{
  deliveryProfiles(first: 20) {
    edges {
      node {
        id
        name
        default
        profileLocationGroups {
          locationGroup {
            id
            locations(first: 20) { edges { node { id name } } }
          }
          locationGroupZones(first: 25) {
            edges {
              node {
                zone {
                  id
                  name
                  countries { code { countryCode } }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

# Fetch methodDefinitions one zone at a time to stay cheap.
METHOD_DEFS_QUERY = """
query($profileId: ID!, $locationGroupId: ID!, $zoneId: ID!) {
  deliveryProfile(id: $profileId) {
    profileLocationGroups {
      locationGroup { id }
      locationGroupZones(first: 25) {
        edges {
          node {
            zone { id }
            methodDefinitions(first: 50) {
              edges {
                node {
                  id
                  name
                  active
                  description
                  rateProvider {
                    __typename
                    ... on DeliveryRateDefinition {
                      price { amount currencyCode }
                    }
                    ... on DeliveryParticipant {
                      carrierService { name }
                    }
                  }
                  methodConditions {
                    field
                    operator
                    conditionCriteria {
                      __typename
                      ... on MoneyV2 { amount currencyCode }
                      ... on Weight { value unit }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def main() -> int:
    cfg = load_config()
    client = ShopifyClient(cfg)

    profiles = client.graphql(PROFILES_QUERY)
    out = {"profiles": []}

    for p_edge in profiles["deliveryProfiles"]["edges"]:
        profile = p_edge["node"]
        p_out = {
            "id": profile["id"],
            "name": profile["name"],
            "default": profile["default"],
            "location_groups": [],
        }

        # Second query: fetch methodDefinitions for this profile
        try:
            md = client.graphql(METHOD_DEFS_QUERY, {
                "profileId": profile["id"],
                "locationGroupId": "",
                "zoneId": "",
            })
            method_profile = md["deliveryProfile"]
        except Exception as e:
            log.warning("Could not fetch methods for profile %s: %s", profile["name"], e)
            method_profile = None

        method_map: dict[str, list] = {}
        if method_profile:
            for plg in method_profile.get("profileLocationGroups") or []:
                for z_edge in (plg.get("locationGroupZones") or {}).get("edges", []):
                    zn = z_edge["node"]
                    zid = zn["zone"]["id"]
                    methods = []
                    for m_edge in (zn.get("methodDefinitions") or {}).get("edges", []):
                        methods.append(m_edge["node"])
                    method_map[zid] = methods

        for plg in profile.get("profileLocationGroups") or []:
            lg = plg.get("locationGroup") or {}
            locs = [e["node"]["name"]
                    for e in (lg.get("locations") or {}).get("edges", [])]
            lg_out = {"location_group_id": lg.get("id"),
                      "locations": locs,
                      "zones": []}
            for z_edge in (plg.get("locationGroupZones") or {}).get("edges", []):
                zn = z_edge["node"]
                zone = zn["zone"]
                z_out = {
                    "id": zone["id"],
                    "name": zone["name"],
                    "countries": [c["code"]["countryCode"] for c in zone.get("countries", [])],
                    "methods": method_map.get(zone["id"], []),
                }
                lg_out["zones"].append(z_out)
            p_out["location_groups"].append(lg_out)
        out["profiles"].append(p_out)

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("Wrote %s", OUT.name)

    # Pretty print summary
    print()
    print("=" * 70)
    print("SHIPPING SETTINGS (delivery profiles)")
    print("=" * 70)
    for p in out["profiles"]:
        print(f"\nProfile: {p['name']}  (default={p['default']})")
        for lg in p["location_groups"]:
            if lg["locations"]:
                print(f"  Locations: {', '.join(lg['locations'])}")
            for z in lg["zones"]:
                countries = ", ".join(z["countries"]) or "(no countries)"
                print(f"  Zone: {z['name']}  [{countries}]")
                if not z["methods"]:
                    print("    (no shipping methods)")
                for m in z["methods"]:
                    rp = m.get("rateProvider") or {}
                    tn = rp.get("__typename")
                    if tn == "DeliveryRateDefinition":
                        price_part = f" @ {(rp.get('price') or {}).get('amount','?')} {(rp.get('price') or {}).get('currencyCode','')}"
                    elif tn == "DeliveryParticipant":
                        price_part = f" via carrier {(rp.get('carrierService') or {}).get('name','?')}"
                    else:
                        price_part = f" ({tn})"
                    conds = m.get("methodConditions") or []
                    cond_strs = []
                    for c in conds:
                        cc = c.get("conditionCriteria") or {}
                        if cc.get("__typename") == "MoneyV2":
                            cond_strs.append(f"{c['field']} {c['operator']} ${cc.get('amount')} {cc.get('currencyCode')}")
                        elif cc.get("__typename") == "Weight":
                            cond_strs.append(f"{c['field']} {c['operator']} {cc.get('value')} {cc.get('unit')}")
                        else:
                            cond_strs.append(f"{c['field']} {c['operator']} ?")
                    cond_part = f"   (conditions: {'; '.join(cond_strs)})" if cond_strs else ""
                    active = m.get("active")
                    print(f"    - {m['name']} [active={active}]{price_part}{cond_part}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
