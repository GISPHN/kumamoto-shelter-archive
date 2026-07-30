#!/usr/bin/env python3
"""Apply idempotent compatibility refinements to collect_capacity.py."""

from pathlib import Path


def main() -> int:
    path = Path("scripts/collect_capacity.py")
    text = path.read_text(encoding="utf-8")

    old_pick = '''def pick(obj: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    values = {nkey(k): v for k, v in obj.items()}
    return next((values[nkey(a)] for a in aliases if nkey(a) in values and values[nkey(a)] not in (None, "")), "")
'''
    new_pick = '''def pick(obj: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    values = {nkey(k): v for k, v in obj.items()}
    for alias in aliases:
        alias_key = nkey(alias)
        for key, value in values.items():
            if value in (None, ""):
                continue
            if key == alias_key or alias_key in key or key in alias_key:
                return value
    return ""
'''
    if old_pick in text:
        text = text.replace(old_pick, new_pick, 1)

    old_json = '''          try:data=json.loads(text)
          except json.JSONDecodeError:return
'''
    new_json = '''          try:
            data=json.loads(text)
          except json.JSONDecodeError:
            wrapped=re.search(r"^[^(]+\\((.*)\\)\\s*;?\\s*$",text,re.S)
            if not wrapped:return
            try:data=json.loads(wrapped.group(1))
            except json.JSONDecodeError:return
'''
    if old_json in text:
        text = text.replace(old_json, new_json, 1)

    path.write_text(text, encoding="utf-8")
    print("Capacity collector compatibility refinements applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
