#!/usr/bin/env python3
"""Apply Kumamoto portal Dojo dgrid compatibility patches idempotently."""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path("scripts/collect_shelters.py")
    text = path.read_text(encoding="utf-8")
    changed = False

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

    dgrid_marker = "Dojo dgrid renders the header and every data row as separate"
    if dgrid_marker not in text:
        marker = '''            headers = extracted.get("headers", [])
            rows = extracted.get("rows", [])

            # DOM fallback: iterate visible pagination until the Next control is'''
        replacement = '''            headers = extracted.get("headers", [])
            rows = extracted.get("rows", [])

            # Dojo dgrid renders the header and every data row as separate
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

            print(
                f"Rendered extraction: mode={extracted.get('mode')}; "
                f"all_selected={all_selected}; headers={headers}; rows={len(rows)}"
            )

            # DOM fallback: iterate visible pagination until the Next control is'''
        if marker not in text:
            raise SystemExit("extraction insertion point not found")
        text = text.replace(marker, replacement, 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
        print("Applied Dojo compatibility patch.")
    else:
        print("Dojo compatibility patch is already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
