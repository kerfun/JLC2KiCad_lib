#!/usr/bin/env python3
"""Update the Value property in a .kicad_sym library.

For components where the EasyEDA API returns a Value (resistors, capacitors,
inductors, etc.) the Value property is set to that value (e.g. "100nF").
Components with no API Value (ICs, connectors, MOSFETs, etc.) are left alone.

Usage:
    python3 fix_values.py [LIBRARY_PATH] [--dry-run]
"""

import argparse
import json
import time

import requests
import sexpdata
from sexpdata import Symbol

DEFAULT_LIBRARY_PATH = (
    "/home/kevin/refactored/refactoredWorkspace/library/customSymbols/refactoredLib.kicad_sym"
)
USER_AGENT = {"User-Agent": "curl/8.7.1"}
RATE_LIMIT_SECONDS = 0.3


# ---------------------------------------------------------------------------
# EasyEDA API
# ---------------------------------------------------------------------------

def fetch_component_para(lcsc_id: str) -> dict | None:
    r = requests.get(
        f"https://easyeda.com/api/products/{lcsc_id}/svgs",
        headers=USER_AGENT,
        timeout=10,
    )
    if r.status_code != 200 or not r.content:
        return None
    data = json.loads(r.content.decode())
    if not data.get("success") or not data.get("result"):
        return None

    component_uuid = data["result"][0]["component_uuid"]
    r2 = requests.get(
        f"https://easyeda.com/api/components/{component_uuid}",
        headers=USER_AGENT,
        timeout=10,
    )
    if r2.status_code != 200 or not r2.content:
        return None
    d2 = json.loads(r2.content.decode())
    return d2["result"]["dataStr"]["head"]["c_para"]


# ---------------------------------------------------------------------------
# sexpdata helpers
# ---------------------------------------------------------------------------

def is_node(item, name: str) -> bool:
    return isinstance(item, list) and len(item) >= 1 and item[0] == Symbol(name)


def get_property(symbol_node: list, prop_name: str) -> list | None:
    for item in symbol_node:
        if is_node(item, "property") and len(item) >= 3 and item[1] == prop_name:
            return item
    return None


def get_property_value(symbol_node: list, prop_name: str) -> str | None:
    prop = get_property(symbol_node, prop_name)
    return prop[2] if prop is not None else None


def set_property_value(symbol_node: list, prop_name: str, new_value: str) -> bool:
    prop = get_property(symbol_node, prop_name)
    if prop is not None:
        prop[2] = new_value
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix Value property in a .kicad_sym library using EasyEDA API"
    )
    parser.add_argument(
        "library",
        nargs="?",
        default=DEFAULT_LIBRARY_PATH,
        help="Path to the .kicad_sym file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    library_path = args.library
    print(f"Loading {library_path} ...")
    with open(library_path, "r") as f:
        content = f.read()

    lib = sexpdata.loads(content)

    top_symbols = [item for item in lib if is_node(item, "symbol")]
    total = len(top_symbols)
    print(f"Found {total} top-level symbols\n")

    updated_count = 0
    skipped_count = 0
    no_value_count = 0
    failed_count = 0

    for idx, sym in enumerate(top_symbols):
        symbol_name = sym[1]
        lcsc_id = get_property_value(sym, "LCSC")

        if not lcsc_id:
            print(f"[{idx+1}/{total}] {symbol_name}: SKIP (no LCSC property)")
            skipped_count += 1
            continue

        current_value = get_property_value(sym, "Value") or ""

        print(f"[{idx+1}/{total}] {symbol_name} ({lcsc_id}): fetching API ...", end=" ", flush=True)
        try:
            c_para = fetch_component_para(lcsc_id)
        except Exception as exc:
            print(f"ERROR ({exc})")
            failed_count += 1
            continue

        if not c_para:
            print("FAIL (no data returned)")
            failed_count += 1
            continue

        api_value = c_para.get("Value", "").strip()

        if not api_value:
            print("SKIP (no Value in API)")
            no_value_count += 1
            time.sleep(RATE_LIMIT_SECONDS)
            continue

        if current_value == api_value:
            print(f"OK (already \"{api_value}\")")
            skipped_count += 1
            time.sleep(RATE_LIMIT_SECONDS)
            continue

        set_property_value(sym, "Value", api_value)
        print(f"\"{current_value}\" -> \"{api_value}\"")
        updated_count += 1

        time.sleep(RATE_LIMIT_SECONDS)

    print(
        f"\nSummary: {updated_count} updated, {skipped_count} skipped, "
        f"{no_value_count} no API value, {failed_count} failed"
    )

    if args.dry_run:
        print("(dry-run: not writing file)")
        return

    print(f"Writing {library_path} ...")
    with open(library_path, "w") as f:
        f.write(sexpdata.dumps(lib))
    print("Done.")


if __name__ == "__main__":
    main()
