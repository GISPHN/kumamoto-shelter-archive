#!/usr/bin/env python3
"""Refine the runtime Dojo dgrid extraction to the shelter grid only."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path("scripts/collect_shelters.py")
    text = path.read_text(encoding="utf-8")

    start_marker = "            # Dojo dgrid uses virtual scrolling and recycles row elements."
    end_marker = "            print("
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit("virtual dgrid block not found")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit("rendered extraction print block not found")

    replacement = '''            # Dojo dgrid uses virtual scrolling and recycles row elements.
            # Restrict extraction to the dgrid that owns the shelter header, then
            # scroll through its virtual buffer and accumulate stable row IDs.
            dgrid_extracted = await page.evaluate(
                r"""
                async () => {
                  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                  const headerRow = Array.from(document.querySelectorAll('tr')).find(tr =>
                    Array.from(tr.children).some(cell =>
                      cell.tagName === 'TH' && norm(cell.textContent).includes('避難所名')
                    )
                  );
                  if (!headerRow) return {headers: [], rows: [], scrolled: false, error: 'header_not_found'};

                  const headers = Array.from(headerRow.children)
                    .filter(cell => cell.tagName === 'TH')
                    .map(cell => norm(cell.textContent));
                  const headerContainer = headerRow.closest('.dgrid-header');
                  const root =
                    (headerContainer && headerContainer.parentElement) ||
                    headerRow.closest('[role="grid"]') ||
                    headerRow.closest('.dgrid');
                  if (!root) {
                    return {headers, rows: [], scrolled: false, error: 'shelter_grid_root_not_found'};
                  }

                  const scrollers = Array.from(root.querySelectorAll('.dgrid-scroller'));
                  const scroller = scrollers.sort(
                    (a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight)
                  )[0] || null;
                  const seen = new Map();
                  const observedRowIds = new Set();

                  const collect = () => {
                    let rowNodes = Array.from(root.querySelectorAll('.dgrid-row'));
                    if (!rowNodes.length) {
                      rowNodes = Array.from(root.querySelectorAll('tr')).filter(tr =>
                        Array.from(tr.children).some(cell => cell.tagName === 'TD')
                      );
                    }
                    for (const rowNode of rowNodes) {
                      const tr = rowNode.tagName === 'TR' ? rowNode : rowNode.querySelector('tr');
                      if (!tr) continue;
                      const cells = Array.from(tr.children)
                        .filter(cell => cell.tagName === 'TD')
                        .map(cell => norm(cell.textContent));
                      if (headers.length < 5 || cells.length < headers.length) continue;
                      const values = cells.slice(0, headers.length);
                      if (!values[1] || values[1] === '避難所名') continue;
                      // The source table is the opened shelter list. Reject rows
                      // that do not explicitly report an opened state.
                      if (!values[2].includes('開設') || values[2].includes('未開設')) continue;
                      const rowId =
                        rowNode.getAttribute('data-id') ||
                        rowNode.id ||
                        tr.getAttribute('data-id') ||
                        tr.id ||
                        JSON.stringify(values);
                      observedRowIds.add(rowId);
                      if (!seen.has(rowId)) seen.set(rowId, values);
                    }
                  };

                  collect();
                  if (!scroller) {
                    return {
                      headers,
                      rows: Array.from(seen.values()),
                      scrolled: false,
                      error: 'scroller_not_found',
                      rootId: root.id || '',
                      rootClass: root.className || '',
                      ariaRowCount: root.getAttribute('aria-rowcount') || '',
                      rowIdCount: observedRowIds.size,
                    };
                  }

                  scroller.scrollIntoView({block: 'center'});
                  scroller.scrollTop = 0;
                  scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                  await sleep(800);
                  collect();

                  let stableBottomRounds = 0;
                  let previousCount = -1;
                  for (let iteration = 0; iteration < 500; iteration += 1) {
                    collect();
                    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
                    const currentTop = scroller.scrollTop;
                    const atBottom = currentTop >= maxTop - 2;

                    if (atBottom) {
                      await sleep(700);
                      collect();
                      const expandedMaxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
                      if (expandedMaxTop > maxTop + 2) {
                        scroller.scrollTop = expandedMaxTop;
                        scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                        await sleep(500);
                        continue;
                      }
                      if (seen.size === previousCount) {
                        stableBottomRounds += 1;
                      } else {
                        stableBottomRounds = 0;
                      }
                      previousCount = seen.size;
                      if (stableBottomRounds >= 4) break;

                      scroller.scrollTop = Math.max(0, maxTop - Math.max(100, scroller.clientHeight * 0.2));
                      scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                      await sleep(250);
                      scroller.scrollTop = maxTop;
                      scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                    } else {
                      const step = Math.max(300, Math.floor(scroller.clientHeight * 0.7));
                      scroller.scrollTop = Math.min(maxTop, currentTop + step);
                      scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                    }
                    await sleep(400);
                  }

                  collect();
                  return {
                    headers,
                    rows: Array.from(seen.values()),
                    scrolled: true,
                    rootId: root.id || '',
                    rootClass: root.className || '',
                    ariaRowCount: root.getAttribute('aria-rowcount') || '',
                    scrollerClass: scroller.className || '',
                    scrollHeight: scroller.scrollHeight,
                    clientHeight: scroller.clientHeight,
                    rowIdCount: observedRowIds.size,
                    renderedRowCount: root.querySelectorAll('.dgrid-row').length,
                  };
                }
                """
            )
            if len(dgrid_extracted.get("rows", [])) > len(rows):
                headers = dgrid_extracted.get("headers", headers)
                rows = dgrid_extracted.get("rows", rows)

'''

    text = text[:start] + replacement + text[end:]

    old_print = '''            print(
                f"Rendered extraction: mode={extracted.get('mode')}; "
                f"all_selected={all_selected}; headers={headers}; rows={len(rows)}"
            )'''
    new_print = '''            dgrid_meta = {
                key: value
                for key, value in dgrid_extracted.items()
                if key not in {"headers", "rows"}
            }
            print(
                f"Rendered extraction: mode={extracted.get('mode')}; "
                f"all_selected={all_selected}; headers={headers}; rows={len(rows)}; "
                f"dgrid_meta={json.dumps(dgrid_meta, ensure_ascii=False, sort_keys=True)}"
            )'''
    if old_print in text:
        text = text.replace(old_print, new_print, 1)

    path.write_text(text, encoding="utf-8")
    print("Refined shelter dgrid extraction to the owning grid and stable row IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
