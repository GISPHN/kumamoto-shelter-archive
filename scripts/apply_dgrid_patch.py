#!/usr/bin/env python3
"""Apply Kumamoto portal compatibility and full snapshot patches idempotently."""

from __future__ import annotations

from pathlib import Path


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        return text, False
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"end marker not found after: {start_marker}")
    current = text[start:end]
    if current == replacement:
        return text, False
    return text[:start] + replacement + text[end:], True


def main() -> int:
    path = Path("scripts/collect_shelters.py")
    text = path.read_text(encoding="utf-8")
    changed = False

    # The daily source is the portal's opened shelter list. Layer 14 is a map
    # display control and must not be used as the source of the list status.
    if "p=evacuation%2Fshelter&l=14-0&" in text:
        text = text.replace(
            "p=evacuation%2Fshelter&l=14-0&",
            "p=evacuation%2Fshelter&l=15-0&",
            1,
        )
        changed = True

    # The Dojo router occasionally leaves only the outer shell. Wait for the
    # semantic table header and retry the complete navigation when necessary.
    load_marker = "Dojo shelter view did not render"
    if load_marker not in text:
        old_load = '''            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
'''
        new_load = '''            rendered = False
            last_load_error = ""
            for load_attempt in range(1, 5):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await page.wait_for_function(
                        """() => Array.from(document.querySelectorAll('th'))
                          .some(th => (th.textContent || '').includes('避難所名'))""",
                        timeout=min(timeout_ms, 30000),
                    )
                    rendered = True
                    print(f"Dojo shelter view rendered on attempt {load_attempt}.")
                    break
                except Exception as exc:
                    last_load_error = f"{type(exc).__name__}: {exc}"
                    print(
                        f"Dojo shelter view did not render on attempt {load_attempt}/4: "
                        f"{last_load_error}"
                    )
                    if load_attempt < 4:
                        await page.wait_for_timeout(3000)
            if not rendered:
                raise RuntimeError(
                    "Dojo shelter view did not render after 4 attempts. "
                    f"Last error: {last_load_error}"
                )
            await page.wait_for_timeout(1500)
'''
        if old_load not in text:
            raise SystemExit("page load block not found")
        text = text.replace(old_load, new_load, 1)
        changed = True

    # Do not click the site's "全ての避難所" button. It changes the map layer,
    # not the meaning of the opened shelter table used for the daily observation.
    all_mode_start = '            # Explicitly select the site\'s "all shelters" mode.'
    all_mode_end = "            # Attempt to select the DataTables"
    all_mode_replacement = '''            # Keep the portal in opened shelter list mode. The site's
            # "全ての避難所" control is a map layer selector and is not used here.
            all_selected = False

'''
    text, did_replace = replace_between(
        text,
        all_mode_start,
        all_mode_end,
        all_mode_replacement,
    )
    changed = changed or did_replace

    # Dojo dgrid virtualizes its rows. Only the first rendered buffer was being
    # captured previously. Scroll the dgrid scroller from top to bottom and
    # accumulate every unique row as the DOM buffer is recycled.
    virtual_block = '''            # Dojo dgrid uses virtual scrolling and recycles row elements.
            # Scroll through the grid and accumulate every unique rendered row.
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
                  if (!headerRow) return {headers: [], rows: [], scrolled: false};

                  const headers = Array.from(headerRow.children)
                    .filter(cell => cell.tagName === 'TH')
                    .map(cell => norm(cell.textContent));
                  const root = headerRow.closest('.dgrid') || document;
                  const scrollers = Array.from(root.querySelectorAll('.dgrid-scroller'));
                  const scroller = scrollers.sort(
                    (a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight)
                  )[0] || null;
                  const seen = new Map();

                  const collect = () => {
                    const rows = Array.from(root.querySelectorAll('tr'));
                    for (const tr of rows) {
                      const cells = Array.from(tr.children)
                        .filter(cell => cell.tagName === 'TD')
                        .map(cell => norm(cell.textContent));
                      if (headers.length < 5 || cells.length < headers.length) continue;
                      const values = cells.slice(0, headers.length);
                      if (!values.some(Boolean)) continue;
                      if (!values[1] || values[1] === '避難所名') continue;
                      const key = JSON.stringify(values);
                      if (!seen.has(key)) seen.set(key, values);
                    }
                  };

                  collect();
                  if (!scroller) {
                    return {headers, rows: Array.from(seen.values()), scrolled: false};
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

                      // Nudge the virtual grid so a final deferred page is requested.
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
                    scrollHeight: scroller.scrollHeight,
                    clientHeight: scroller.clientHeight,
                  };
                }
                """
            )
            if len(dgrid_extracted.get("rows", [])) > len(rows):
                headers = dgrid_extracted.get("headers", headers)
                rows = dgrid_extracted.get("rows", rows)

'''
    dgrid_starts = [
        "            # Dojo dgrid renders the header and each data row in separate,",
        "            # Dojo dgrid renders the header and every data row as separate",
        "            # Dojo dgrid uses virtual scrolling and recycles row elements.",
    ]
    dgrid_start = next((marker for marker in dgrid_starts if marker in text), None)
    if dgrid_start is None:
        raise SystemExit("dgrid extraction block not found")
    text, did_replace = replace_between(text, dgrid_start, "            print(", virtual_block)
    changed = changed or did_replace

    # If virtual dgrid rows were obtained, do not enter the conventional tbody
    # pagination fallback, which cannot read dgrid and previously erased results.
    old_condition = '            if extracted.get("mode") != "datatable":\n'
    new_condition = '            if extracted.get("mode") != "datatable" and not rows:\n'
    if old_condition in text:
        text = text.replace(old_condition, new_condition, 1)
        changed = True

    # Do not require at least one opened shelter. Zero opened shelters is a valid
    # daily state as long as the dgrid header was rendered successfully.
    no_open_block = '''        web_rows = normalized_rows
        if not web_rows:
            raise RuntimeError("Webの開設避難所一覧から1件も取得できませんでした。")

'''
    no_open_replacement = '''        web_rows = normalized_rows
        if not web_rows:
            print("Opened shelter table contains zero rows; recording all reference facilities as inactive.")

'''
    if no_open_block in text:
        text = text.replace(no_open_block, no_open_replacement, 1)
        changed = True

    # Add the full reference master overlay if it is not already present.
    master_marker = "Web開設一覧に掲載なし"
    if master_marker not in text:
        old_main = '''        for row in normalized_rows:
            enrichment = reference_matcher.enrich(row)
            row.update(enrichment)
            row["shelter_id"] = tracking_id(row["web_shelter_id"], enrichment)
            row["record_hash"] = record_hash(row)

        validate_collection(normalized_rows, args.minimum_rows)
'''
        new_main = '''        for row in normalized_rows:
            enrichment = reference_matcher.enrich(row)
            row.update(enrichment)
            row["shelter_id"] = tracking_id(row["web_shelter_id"], enrichment)
            row["record_hash"] = record_hash(row)

        # Build the complete daily population from the supplied reference CSV
        # and overlay the rows currently shown in the opened shelter table.
        web_rows = normalized_rows
        if not web_rows:
            print("Opened shelter table contains zero rows; recording all reference facilities as inactive.")

        master_rows: dict[tuple[str, ...], dict[str, str]] = {}
        for reference_group in reference_matcher.by_name_address.values():
            ordered_group = sorted(reference_group, key=lambda item: item["共通ID"])
            primary = ordered_group[0]
            address_without_prefecture = primary["住所"].replace("熊本県", "", 1)
            municipality_match = re.match(r"(.+?(?:市|町|村))", address_without_prefecture)
            municipality = municipality_match.group(1) if municipality_match else ""
            synthetic_raw = {
                "市町村": municipality,
                "避難所名": primary["施設・場所名"],
                "開設状況": "未開設（Web開設一覧に掲載なし）",
                "混雑状況": "",
                "住所": primary["住所"],
                "ルート検索": "",
            }
            master_row = row_to_normalized(
                synthetic_raw,
                snapshot_date,
                retrieved_at,
                result.source_updated_at_text,
                args.url,
            )
            enrichment = reference_matcher.enrich(master_row)
            master_row.update(enrichment)
            master_row["shelter_id"] = tracking_id(master_row["web_shelter_id"], enrichment)
            master_row["record_hash"] = record_hash(master_row)
            common_ids = tuple(
                sorted(value for value in enrichment.get("reference_common_ids", "").split(";") if value)
            )
            if not common_ids:
                raise RuntimeError(
                    f"参照CSV施設を自己照合できませんでした: {primary['施設・場所名']}"
                )
            master_rows[common_ids] = master_row

        unmatched_or_extra_web_rows: list[dict[str, str]] = []
        for web_row in web_rows:
            common_ids = tuple(
                sorted(
                    value
                    for value in web_row.get("reference_common_ids", "").split(";")
                    if value
                )
            )
            if common_ids and common_ids in master_rows:
                master_rows[common_ids] = web_row
            else:
                unmatched_or_extra_web_rows.append(web_row)

        normalized_rows = list(master_rows.values()) + unmatched_or_extra_web_rows
        normalized_rows.sort(
            key=lambda row: (
                normalize_text(row.get("municipality", "")),
                normalize_text(row.get("shelter_name", "")),
                row.get("shelter_id", ""),
            )
        )
        print(
            f"Full snapshot: reference_groups={len(master_rows)}, "
            f"opened_web_rows={len(web_rows)}, extra_web_rows={len(unmatched_or_extra_web_rows)}, "
            f"total={len(normalized_rows)}"
        )

        validate_collection(normalized_rows, args.minimum_rows)
'''
        if old_main not in text:
            raise SystemExit("main enrichment block not found")
        text = text.replace(old_main, new_main, 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
        print("Applied portal virtual dgrid and full snapshot patch.")
    else:
        print("Portal virtual dgrid and full snapshot patch is already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
