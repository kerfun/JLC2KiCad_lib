#!/usr/bin/env python3
"""Update ki_keywords in a .kicad_sym library file.

Parses the library with sexpdata, then for each symbol fetches EasyEDA API
data to add Manufacturer Part and Value to ki_keywords, and adds/corrects
the Manufacturer # property.

Usage:
    python3 cleanup_keywords.py [LIBRARY_PATH] [--dry-run]
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
# EasyEDA API helpers
# ---------------------------------------------------------------------------

def fetch_component_para(lcsc_id: str) -> dict | None:
    """Return the c_para dict for an LCSC part number, or None on failure."""
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


def build_keywords(lcsc_id: str, c_para: dict) -> str:
    """Build combined ki_keywords string: LCSC MPN Value (deduped)."""
    parts = [lcsc_id]
    mfr_part = c_para.get("Manufacturer Part", "").strip()
    value = c_para.get("Value", "").strip()
    if mfr_part and mfr_part not in parts:
        parts.append(mfr_part)
    if value and value not in parts:
        parts.append(value)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# sexpdata helpers
# ---------------------------------------------------------------------------

def is_node(item, name: str) -> bool:
    """True if item is a list whose first element is Symbol(name)."""
    return isinstance(item, list) and len(item) >= 1 and item[0] == Symbol(name)


def get_property(symbol_node: list, prop_name: str) -> list | None:
    """Return the property sub-list for prop_name, or None."""
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


def make_hidden_property(name: str, value: str) -> list:
    """Build a hidden property node matching KiCad 8 format."""
    return [
        Symbol("property"),
        name,
        value,
        [Symbol("at"), 0, 0, 0],
        [Symbol("show_name"), Symbol("no")],
        [Symbol("do_not_autoplace"), Symbol("no")],
        [Symbol("hide"), Symbol("yes")],
        [Symbol("effects"),
            [Symbol("font"),
                [Symbol("size"), 1.27, 1.27],
            ],
        ],
    ]


def insert_property_after(symbol_node: list, after_name: str, new_prop: list) -> None:
    """Insert new_prop right after the property named after_name.

    Falls back to appending before the first nested (symbol ...) sub-node.
    """
    for i, item in enumerate(symbol_node):
        if is_node(item, "property") and len(item) >= 3 and item[1] == after_name:
            symbol_node.insert(i + 1, new_prop)
            return
    # Fallback: insert before first nested symbol sub-block
    for i, item in enumerate(symbol_node):
        if is_node(item, "symbol"):
            symbol_node.insert(i, new_prop)
            return
    symbol_node.append(new_prop)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update ki_keywords and Manufacturer # in a .kicad_sym library"
    )
    parser.add_argument(
        "library",
        nargs="?",
        default=DEFAULT_LIBRARY_PATH,
        help="Path to the .kicad_sym file (default: refactoredLib)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    library_path = args.library
    print(f"Loading {library_path} ...")
    with open(library_path, "r") as f:
        content = f.read()

    lib = sexpdata.loads(content)

    # Top-level symbols are direct children of kicad_symbol_lib
    top_symbols = [item for item in lib if is_node(item, "symbol")]
    total = len(top_symbols)
    print(f"Found {total} top-level symbols\n")

    updated_count = 0
    skipped_count = 0
    failed_count = 0

    for idx, sym in enumerate(top_symbols):
        symbol_name = sym[1]
        lcsc_id = get_property_value(sym, "LCSC")

        if not lcsc_id:
            print(f"[{idx+1}/{total}] {symbol_name}: SKIP (no LCSC property)")
            skipped_count += 1
            continue

        current_kw = get_property_value(sym, "ki_keywords") or ""
        already_full = len(current_kw.strip().split()) > 1
        already_has_mfr = get_property(sym, "Manufacturer #") is not None

        if already_full and already_has_mfr:
            print(f"[{idx+1}/{total}] {symbol_name} ({lcsc_id}): SKIP (already complete)")
            skipped_count += 1
            continue

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

        new_kw = build_keywords(lcsc_id, c_para)
        mfr_part = c_para.get("Manufacturer Part", "").strip()
        changed = False

        # Update ki_keywords
        if current_kw != new_kw:
            if not set_property_value(sym, "ki_keywords", new_kw):
                # Property missing entirely — add it
                insert_property_after(sym, "LCSC", make_hidden_property("ki_keywords", new_kw))
            changed = True

        # Update or add Manufacturer #
        if mfr_part:
            if already_has_mfr:
                current_mfr = get_property_value(sym, "Manufacturer #")
                if current_mfr != mfr_part:
                    set_property_value(sym, "Manufacturer #", mfr_part)
                    changed = True
            else:
                insert_property_after(sym, "LCSC", make_hidden_property("Manufacturer #", mfr_part))
                changed = True

        if changed:
            print(f'-> "{new_kw}"')
            updated_count += 1
        else:
            print("OK (no change needed)")
            skipped_count += 1

        time.sleep(RATE_LIMIT_SECONDS)

    print(f"\nSummary: {updated_count} updated, {skipped_count} skipped, {failed_count} failed")

    if args.dry_run:
        print("(dry-run: not writing file)")
        return

    print(f"Writing {library_path} ...")
    with open(library_path, "w") as f:
        f.write(sexpdata.dumps(lib))
    print("Done.")


if __name__ == "__main__":
    main()
