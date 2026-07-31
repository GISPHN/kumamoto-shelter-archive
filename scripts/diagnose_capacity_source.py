#!/usr/bin/env python3
"""Diagnose where Kumamoto portal map capacity data is stored.

This is a focused one-municipality probe. It records resource URLs, selected
response snippets, JavaScript keyword hits, DOM/map marker metadata, Dojo
widgets, dgrid backing objects and iframe summaries. Small text reports are
committed by the workflow; complete payloads are kept as an artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

KEYWORDS = (
    "定員", "収容定員", "収容可能人数", "収容人数", "capacity", "capacities",
    "shelter", "evacuation", "hinan", "hinanjo", "congestion", "混雑",
)


def build_url(code: str) -> str:
    query = urlencode(
        {
            "p": "evacuation/shelter",
            "l": "15-1",
            "ll": "32.8117569,130.7430407",
            "z": "18",
            "municipalityCd": code,
        }
    )
    return f"https://portal.bousai.pref.kumamoto.jp/sp.html?{query}"


def safe_name(index: int, url: str, suffix: str) -> str:
    tail = re.sub(r"[^0-9A-Za-z._-]+", "_", url.rsplit("/", 1)[-1])[-100:] or "resource"
    return f"{index:04d}_{tail}.{suffix}"


async def main_async(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    output = Path(args.output_dir)
    payload_dir = output / "payloads"
    script_dir = output / "scripts"
    output.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    keyword_hits: list[dict[str, Any]] = []
    response_tasks: set[asyncio.Task[Any]] = set()
    resource_index = 0

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 1200},
        )
        page = await context.new_page()
        page.set_default_timeout(args.timeout_ms)

        async def inspect_response(response: Any) -> None:
            nonlocal resource_index
            try:
                request = response.request
                content_type = (response.headers.get("content-type") or "").lower()
                entry: dict[str, Any] = {
                    "url": response.url,
                    "status": response.status,
                    "resource_type": request.resource_type,
                    "method": request.method,
                    "content_type": content_type,
                }
                manifest.append(entry)
                interesting_type = request.resource_type in {"xhr", "fetch", "script", "document"}
                interesting_url = any(token in response.url.lower() for token in ("shelter", "evac", "hinan", "map", "layer", "point", "facility"))
                if not (interesting_type or interesting_url):
                    return
                body = await response.body()
                entry["body_bytes"] = len(body)
                if not body or len(body) > args.max_body_bytes:
                    return
                text = body.decode("utf-8", errors="replace")
                lowered = text.casefold()
                hits = [keyword for keyword in KEYWORDS if keyword.casefold() in lowered]
                resource_index += 1
                if request.resource_type == "script" or "javascript" in content_type:
                    path = script_dir / safe_name(resource_index, response.url, "js")
                else:
                    path = payload_dir / safe_name(resource_index, response.url, "txt")
                path.write_text(text, encoding="utf-8")
                entry["saved_path"] = path.as_posix()
                if hits:
                    snippets: list[str] = []
                    for keyword in hits:
                        pos = lowered.find(keyword.casefold())
                        if pos >= 0:
                            snippets.append(text[max(0, pos - 250) : pos + 500])
                    keyword_hits.append(
                        {
                            "url": response.url,
                            "resource_type": request.resource_type,
                            "content_type": content_type,
                            "keywords": hits,
                            "snippets": snippets[:12],
                            "saved_path": path.as_posix(),
                        }
                    )
            except Exception as exc:
                manifest.append({"url": getattr(response, "url", ""), "inspection_error": f"{type(exc).__name__}: {exc}"})

        def on_response(response: Any) -> None:
            task = asyncio.create_task(inspect_response(response))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        page.on("response", on_response)
        url = build_url(args.municipality_code)
        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        await page.wait_for_timeout(4000)

        # Select all shelters using text, associated labels, checkboxes and radios.
        click_report = await page.evaluate(
            r"""
            async () => {
              const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
              const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
              const report = [];
              const candidates = Array.from(document.querySelectorAll('label, button, a, span, div'))
                .filter(el => norm(el.textContent) === '全ての避難所');
              for (const el of candidates) {
                try {
                  let target = el;
                  if (el.tagName === 'LABEL' && el.htmlFor) target = document.getElementById(el.htmlFor) || el;
                  target.click();
                  report.push({tag: el.tagName, id: el.id || '', className: String(el.className || ''), clicked: true});
                  await sleep(1000);
                } catch (error) {
                  report.push({tag: el.tagName, id: el.id || '', className: String(el.className || ''), clicked: false, error: String(error)});
                }
              }
              return report;
            }
            """
        )
        await page.wait_for_timeout(5000)

        # Attempt representative clicks: first all-shelter table rows, DOM markers,
        # SVG vector features, OpenLayers marker images and accessible map elements.
        interaction_report = await page.evaluate(
            r"""
            async () => {
              const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
              const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
              const selectors = [
                '.dgrid-row',
                '.olLayerMarkers img', '.olLayerDiv img', '.ol-marker',
                '.leaflet-marker-icon', '.mapboxgl-marker',
                'svg path', 'svg circle', 'svg image',
                '[class*="marker"]', '[class*="Marker"]',
                '[role="button"][aria-label*="避難"]',
              ];
              const result = [];
              for (const selector of selectors) {
                const elements = Array.from(document.querySelectorAll(selector)).slice(0, 8);
                for (const el of elements) {
                  try {
                    const before = norm(document.body.innerText);
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                    await sleep(450);
                    const after = norm(document.body.innerText);
                    const capacityText = Array.from(document.querySelectorAll('body *'))
                      .filter(node => /(?:収容可能人数|収容定員|収容人数|定員)/.test(norm(node.textContent)))
                      .map(node => norm(node.textContent)).sort((a,b) => a.length - b.length).slice(0, 10);
                    result.push({selector, tag: el.tagName, id: el.id || '', className: String(el.className || ''), text: norm(el.textContent).slice(0,200), bodyChanged: before !== after, capacityText});
                    if (capacityText.length) return result;
                  } catch (error) {
                    result.push({selector, error: String(error)});
                  }
                }
              }
              return result;
            }
            """
        )
        await page.wait_for_timeout(2000)

        dom_report = await page.evaluate(
            r"""
            () => {
              const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
              const simplify = value => {
                if (value === null || value === undefined) return value;
                if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value;
                if (Array.isArray(value)) return {type: 'array', length: value.length, sample: value.slice(0, 3).map(v => typeof v === 'object' ? Object.keys(v || {}).slice(0,30) : v)};
                if (typeof value === 'object') return {type: value.constructor && value.constructor.name || 'object', keys: Object.keys(value).slice(0, 80)};
                return String(value);
              };
              const capacityNodes = Array.from(document.querySelectorAll('body *'))
                .filter(el => /(?:収容可能人数|収容定員|収容人数|定員)/.test(norm(el.textContent)))
                .map(el => ({tag: el.tagName, id: el.id || '', className: String(el.className || ''), text: norm(el.textContent).slice(0,1000)}))
                .sort((a,b) => a.text.length - b.text.length).slice(0, 100);
              const markerSelectors = ['.olLayerMarkers img','.olLayerDiv img','.ol-marker','.leaflet-marker-icon','.mapboxgl-marker','svg path','svg circle','svg image','[class*="marker"]','[class*="Marker"]'];
              const markerSummary = {};
              for (const selector of markerSelectors) {
                markerSummary[selector] = Array.from(document.querySelectorAll(selector)).slice(0,100).map(el => ({
                  tag: el.tagName, id: el.id || '', className: String(el.className || ''),
                  src: el.getAttribute('src') || '', title: el.getAttribute('title') || '',
                  alt: el.getAttribute('alt') || '', ariaLabel: el.getAttribute('aria-label') || '',
                  outerHTML: el.outerHTML.slice(0,1000),
                }));
              }
              const globals = Object.keys(window).filter(key => /shelter|evac|hinan|map|layer|facility|idis/i.test(key)).slice(0,500)
                .map(key => { try { return {key, value: simplify(window[key])}; } catch(e) { return {key, error: String(e)}; } });
              let widgets = [];
              try {
                const registry = window.dijit && window.dijit.registry;
                if (registry && registry.toArray) widgets = registry.toArray().map(widget => ({
                  id: widget.id || '', declaredClass: widget.declaredClass || '',
                  keys: Object.keys(widget).slice(0,100),
                  store: simplify(widget.store), collection: simplify(widget.collection), data: simplify(widget.data),
                }));
              } catch (e) { widgets = [{error: String(e)}]; }
              const dgrids = Array.from(document.querySelectorAll('.dgrid')).map(root => ({
                id: root.id || '', className: String(root.className || ''), keys: Object.keys(root).slice(0,100),
                html: root.outerHTML.slice(0,3000),
              }));
              const iframes = Array.from(document.querySelectorAll('iframe')).map(frame => ({src: frame.src, id: frame.id, className: String(frame.className || '')}));
              const resources = performance.getEntriesByType('resource').map(entry => ({name: entry.name, initiatorType: entry.initiatorType, transferSize: entry.transferSize}));
              return {title: document.title, url: location.href, capacityNodes, markerSummary, globals, widgets, dgrids, iframes, resources, bodyText: norm(document.body.innerText).slice(0,100000)};
            }
            """
        )

        await page.screenshot(path=str(output / "page.png"), full_page=True)
        (output / "page.html").write_text(await page.content(), encoding="utf-8")
        if response_tasks:
            await asyncio.gather(*list(response_tasks), return_exceptions=True)
        await context.close()
        await browser.close()

    manifest.sort(key=lambda item: (item.get("resource_type", ""), item.get("url", "")))
    report = {
        "source_url": build_url(args.municipality_code),
        "click_report": click_report,
        "interaction_report": interaction_report,
        "dom_report": dom_report,
        "resource_manifest": manifest,
        "keyword_hits": keyword_hits,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines = [
        f"source_url={report['source_url']}",
        f"resources={len(manifest)}",
        f"saved_resources={sum(bool(item.get('saved_path')) for item in manifest)}",
        f"keyword_hit_resources={len(keyword_hits)}",
        f"capacity_nodes={len(dom_report.get('capacityNodes', []))}",
        f"iframes={len(dom_report.get('iframes', []))}",
        f"dgrids={len(dom_report.get('dgrids', []))}",
        f"interactions={len(interaction_report)}",
    ]
    for hit in keyword_hits[:30]:
        summary_lines.append(f"HIT {hit['resource_type']} {','.join(hit['keywords'])} {hit['url']}")
    for node in dom_report.get("capacityNodes", [])[:20]:
        summary_lines.append(f"DOM {node['tag']}#{node['id']}.{node['className']} {node['text'][:500]}")
    (output / "summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--municipality-code", default="431001")
    parser.add_argument("--output-dir", default="debug/capacity_probe")
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--max-body-bytes", type=int, default=12_000_000)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
