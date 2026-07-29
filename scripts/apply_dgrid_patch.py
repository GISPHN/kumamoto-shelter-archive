#!/usr/bin/env python3
"""Apply Kumamoto portal compatibility and full snapshot patches idempotently."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path("scripts/collect_shelters.py")
    text = path.read_text(encoding="utf-8")
    changed = False

    # Layer 15 is the currently opened shelter layer. Layer 14 is the site's
    # explicit all shelter map layer. The list itself still contains open rows.
    if "p=evacuation%2Fshelter&l=15-0&" in text:
        text = text.replace(
            "p=evacuation%2Fshelter&l=15-0&",
            "p=evacuation%2Fshelter&l=14-0&",
            1,
        )
        changed = True

    load_marker = "Dojo shelter view did not render"
    if load_marker not in text:
        old_load = '''            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
'''
        new_load = '''            # The Dojo router occasionally leaves only the outer page shell.
            # Reload until the shelter dgrid header is actually rendered.
            rendered = False
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

    exact_selector = '"[data-idis-layer-id=\'14\']",'
    if exact_selector not in text:
        old_selectors = '''            candidate_selectors = [
                "label:has-text('全ての避難所')",
                "a:has-text('全ての避難所')",
                "button:has-text('全ての避難所')",
                "text=全ての避難所",
            ]'''
        new_selectors = '''            candidate_selectors = [
                "[data-idis-layer-id='14']",
                "label:has-text('全ての避難所')",
                "a:has-text('全ての避難所')",
                "button:has-text('全ての避難所')",
                "text=全ての避難所",
            ]'''
        if old_selectors not in text:
            raise SystemExit("candidate selector block not found")
        text = text.replace(old_selectors, new_selectors, 1)
        changed = True

    wait_marker = "target.classList.contains('is-shown')"
    if wait_marker not in text:
        old_wait = '''                        all_selected = True
                        await page.wait_for_timeout(2500)
                        break'''
        new_wait = '''                        all_selected = True
                        try:
                            await page.wait_for_function(
                                """() => {
                                  const target = document.querySelector('[data-idis-layer-id="14"]');
                                  return target && target.classList.contains('is-shown');
                                }""",
                                timeout=10000,
                            )
                        except Exception:
                            pass
                        await page.wait_for_timeout(5000)
                        break'''
        if old_wait not in text:
            raise SystemExit("post-click wait block not found")
        text = text.replace(old_wait, new_wait, 1)
        changed = True

    old_dgrid = '''            # Dojo dgrid renders the header and every data row as separate
            # table elements. Gather those row tables directly when this yields
            # more records than the generic DataTables/DOM extractor.
            dgrid_extracted = await page.evaluate(
                r"""
                () => {
                  const norm = s => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const tables = Array.from(document.querySelectorAll('table'));
                  const headerTable = tables.find(table =>
                    Array.from(table.querySelectorAll('th')).some(th => norm(th.textContent).includes('避難所名'))
                  );
                  if (!headerTable) return {headers: [], rows: []};
                  const headers = Array.from(headerTable.querySelectorAll('th')).map(th => norm(th.textContent));
                  const rows = tables
                    .filter(table => table !== headerTable)
                    .map(table => Array.from(table.querySelectorAll('td')).map(td => norm(td.textContent)))
                    .filter(cells => cells.length === headers.length && cells.some(Boolean));
                  return {headers, rows};
                }
                """
            )
            if len(dgrid_extracted.get("rows", [])) > len(rows):
                headers = dgrid_extracted.get("headers", headers)
                rows = dgrid_extracted.get("rows", rows)
'''
    new_dgrid = '''            # Dojo dgrid renders the header and each data row in separate,
            # sometimes nested table elements. Parse direct TD children of every
            # TR rather than assuming one conventional table/tbody structure.
            dgrid_extracted = await page.evaluate(
                r"""
                () => {
                  const norm = s => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const headerRow = Array.from(document.querySelectorAll('tr')).find(tr =>
                    Array.from(tr.children).some(cell =>
                      cell.tagName === 'TH' && norm(cell.textContent).includes('避難所名')
                    )
                  );
                  if (!headerRow) return {headers: [], rows: []};
                  const headers = Array.from(headerRow.children)
                    .filter(cell => cell.tagName === 'TH')
                    .map(cell => norm(cell.textContent));
                  const rows = Array.from(document.querySelectorAll('tr'))
                    .map(tr => Array.from(tr.children)
                      .filter(cell => cell.tagName === 'TD')
                      .map(cell => norm(cell.textContent)))
                    .filter(cells => cells.length >= headers.length && headers.length >= 5)
                    .map(cells => cells.slice(0, headers.length))
                    .filter(cells => cells.some(Boolean));
                  return {headers, rows};
                }
                """
            )
            if len(dgrid_extracted.get("rows", [])) > len(rows):
                headers = dgrid_extracted.get("headers", headers)
                rows = dgrid_extracted.get("rows", rows)
'''
    if "Parse direct TD children of every" not in text:
        if old_dgrid not in text:
            raise SystemExit("existing dgrid extraction block not found")
        text = text.replace(old_dgrid, new_dgrid, 1)
        changed = True

    # Do not replace successfully extracted dgrid rows with an empty conventional
    # tbody pagination result.
    if "if collected_rows:\n                    rows = collected_rows" not in text:
        old_fallback = '''                rows = collected_rows

            body_text = normalize_text(await page.locator("body").inner_text())'''
        new_fallback = '''                if collected_rows:
                    rows = collected_rows

            body_text = normalize_text(await page.locator("body").inner_text())'''
        if old_fallback not in text:
            raise SystemExit("pagination fallback assignment not found")
        text = text.replace(old_fallback, new_fallback, 1)
        changed = True

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

        # The portal's dgrid is an opened shelter list. Build the complete daily
        # population from the supplied reference CSV and overlay opened web rows.
        # A reference facility absent from the opened list is recorded as inactive.
        web_rows = normalized_rows
        if not web_rows:
            raise RuntimeError("Webの開設避難所一覧から1件も取得できませんでした。")

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
        print("Applied portal compatibility and full snapshot patch.")
    else:
        print("Portal compatibility and full snapshot patch is already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
