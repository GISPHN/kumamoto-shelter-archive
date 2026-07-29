#!/usr/bin/env python3
"""Ensure that collect_shelters.py defines dgrid_meta before logging it.

refine_dgrid_patch.py replaces the virtual-grid extraction block at runtime.
When the log statement already contains dgrid_meta, that replacement can leave
its variable definition outside the rebuilt block. This idempotent patch inserts
the definition immediately before the rendered-extraction log statement.
"""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path("scripts/collect_shelters.py")
    text = path.read_text(encoding="utf-8")

    definition = '''            dgrid_meta = {
                key: value
                for key, value in dgrid_extracted.items()
                if key not in {"headers", "rows"}
            }
'''
    print_marker = '''            print(
                f"Rendered extraction: mode={extracted.get('mode')}; "
'''

    if definition in text:
        print("dgrid_meta definition is already present.")
        return 0

    if print_marker not in text:
        raise SystemExit("Rendered extraction log statement was not found")

    text = text.replace(print_marker, definition + print_marker, 1)
    path.write_text(text, encoding="utf-8")
    print("Inserted dgrid_meta definition before the extraction log statement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
