#!/usr/bin/env python3
"""Inspect the exact schema of Kumamoto portal shelter JSON endpoints."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

BASE = "https://portal.bousai.pref.kumamoto.jp"
ENDPOINTS = [
    "/data/shelter/shelter.json",
    "/data/shelter/opening_shelter.json",
    "/data/layer/data/15/style.js",
]


def norm_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]", "", text)


CAPACITY_TOKENS = tuple(norm_key(value) for value in (
    "最大収容人数", "収容人数", "収容定員", "定員", "capacity", "maxCapacity",
))
NAME_TOKENS = tuple(norm_key(value) for value in (
    "避難所名", "施設名", "名称", "name", "shelterName", "facilityName",
))
ADDRESS_TOKENS = tuple(norm_key(value) for value in ("住所", "所在地", "address"))
ID_TOKENS = tuple(norm_key(value) for value in ("避難所ID", "施設ID", "id", "shelterId", "facilityId"))
LAT_TOKENS = tuple(norm_key(value) for value in ("緯度", "lat", "latitude", "y"))
LON_TOKENS = tuple(norm_key(value) for value in ("経度", "lon", "lng", "longitude", "x"))


def matches(key: str, tokens: Iterable[str]) -> bool:
    normalized = norm_key(key)
    return any(token == normalized or token in normalized or normalized in token for token in tokens)


def scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def walk(value: Any, path: str = "$", depth: int = 0):
    if depth > 20:
        return
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:100000]):
            yield from walk(child, f"{path}[{index}]", depth + 1)


def summarize_json(data: Any) -> dict[str, Any]:
    key_counts: Counter[str] = Counter()
    key_paths: defaultdict[str, list[str]] = defaultdict(list)
    examples: defaultdict[str, list[Any]] = defaultdict(list)
    object_shapes: Counter[tuple[str, ...]] = Counter()
    candidates: list[dict[str, Any]] = []
    object_count = 0

    for path, obj in walk(data):
        object_count += 1
        shape = tuple(sorted(str(key) for key in obj.keys()))
        object_shapes[shape] += 1
        for key, value in obj.items():
            key_text = str(key)
            key_counts[key_text] += 1
            if len(key_paths[key_text]) < 8:
                key_paths[key_text].append(f"{path}.{key_text}")
            if scalar(value) and len(examples[key_text]) < 8 and value not in examples[key_text]:
                examples[key_text].append(value)

        cap_keys = [str(key) for key in obj if matches(str(key), CAPACITY_TOKENS)]
        if cap_keys and len(candidates) < 80:
            related = {
                str(key): value
                for key, value in obj.items()
                if scalar(value)
                and (
                    matches(str(key), CAPACITY_TOKENS)
                    or matches(str(key), NAME_TOKENS)
                    or matches(str(key), ADDRESS_TOKENS)
                    or matches(str(key), ID_TOKENS)
                    or matches(str(key), LAT_TOKENS)
                    or matches(str(key), LON_TOKENS)
                    or len(obj) <= 30
                )
            }
            candidates.append(
                {
                    "path": path,
                    "capacity_keys": cap_keys,
                    "object_keys": list(obj.keys()),
                    "scalar_values": related,
                }
            )

    interesting_keys = []
    for key, count in key_counts.most_common():
        if any(
            matches(key, tokens)
            for tokens in (CAPACITY_TOKENS, NAME_TOKENS, ADDRESS_TOKENS, ID_TOKENS, LAT_TOKENS, LON_TOKENS)
        ):
            interesting_keys.append(
                {
                    "key": key,
                    "normalized": norm_key(key),
                    "count": count,
                    "paths": key_paths[key],
                    "examples": examples[key],
                }
            )

    shapes = [
        {"count": count, "keys": list(shape)}
        for shape, count in object_shapes.most_common(30)
    ]
    top = {
        "type": type(data).__name__,
        "length": len(data) if isinstance(data, (list, dict)) else None,
        "top_keys": list(data.keys()) if isinstance(data, dict) else None,
    }
    return {
        "top": top,
        "object_count": object_count,
        "interesting_keys": interesting_keys,
        "capacity_candidate_objects": candidates,
        "common_object_shapes": shapes,
    }


def summarize_style(text: str) -> dict[str, Any]:
    lowered = text.casefold()
    tokens = ["最大収容人数", "収容人数", "capacity", "shelter", "混雑"]
    snippets = []
    for token in tokens:
        start = 0
        while len(snippets) < 50:
            pos = lowered.find(token.casefold(), start)
            if pos < 0:
                break
            snippets.append({"token": token, "snippet": text[max(0, pos - 500):pos + 1000]})
            start = pos + len(token)
    return {"length": len(text), "snippets": snippets}


async def run(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    async with async_playwright() as playwright:
        request = await playwright.request.new_context(
            base_url=BASE,
            extra_http_headers={
                "User-Agent": "Mozilla/5.0 KumamotoShelterArchiveSchemaProbe/1.0",
                "Referer": f"{BASE}/sp.html?p=evacuation%2Fshelter",
                "Accept": "application/json,text/javascript,*/*;q=0.8",
            },
        )
        try:
            for endpoint in ENDPOINTS:
                response = await request.get(endpoint, timeout=args.timeout_ms)
                body = await response.body()
                text = body.decode("utf-8", errors="replace")
                entry: dict[str, Any] = {
                    "url": BASE + endpoint,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                    "bytes": len(body),
                }
                if endpoint.endswith(".json"):
                    data = json.loads(text)
                    entry["summary"] = summarize_json(data)
                else:
                    entry["summary"] = summarize_style(text)
                report[endpoint] = entry
        finally:
            await request.dispose()

    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    for endpoint, entry in report.items():
        lines.append(f"ENDPOINT {endpoint} status={entry['status']} bytes={entry['bytes']} content_type={entry['content_type']}")
        summary = entry["summary"]
        if endpoint.endswith(".json"):
            lines.append(f"  top={summary['top']} objects={summary['object_count']}")
            for key in summary["interesting_keys"]:
                lines.append(f"  KEY {key['key']} count={key['count']} examples={key['examples'][:5]} paths={key['paths'][:3]}")
            for candidate in summary["capacity_candidate_objects"][:8]:
                lines.append(f"  CANDIDATE path={candidate['path']} capacity_keys={candidate['capacity_keys']} values={candidate['scalar_values']}")
        else:
            for snippet in summary["snippets"][:10]:
                compact = re.sub(r"\s+", " ", snippet["snippet"])
                lines.append(f"  STYLE {snippet['token']} {compact[:1000]}")
    summary_path = output.with_suffix(".txt")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="debug/capacity_probe/endpoint_schema.json")
    parser.add_argument("--timeout-ms", type=int, default=90000)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
