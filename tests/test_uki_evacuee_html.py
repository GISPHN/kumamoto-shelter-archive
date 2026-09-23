from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from uki_evacuee_html import parse_uki_html


def _page(observation_html: str) -> bytes:
    return f"""
    <html><body>
      <p>2026年09月23日更新</p>
      <p>{observation_html}の避難者数を公表します。</p>
      <h3>避難者数一覧</h3>
      <table>
        <tr><th>No</th><th>避難所名</th><th>住所</th><th>避難者数</th></tr>
        <tr><td>1</td><td>避難所A</td><td>宇城市A</td><td>54</td></tr>
        <tr><td>2</td><td>避難所B</td><td>宇城市B</td><td>60</td></tr>
        <tr><td>合計</td><td></td><td></td><td>114</td></tr>
      </table>
    </body></html>
    """.encode("utf-8")


def test_parser_reads_split_circled_weekday_observation_time() -> None:
    snapshot = parse_uki_html(
        _page("9月22日(<span>㈫曜日</span>)22時00分時点"),
        "https://example.invalid/uki",
    )

    assert snapshot.observed_at_jst == "2026-09-22T22:00+09:00"
    assert snapshot.published_total == 114
    assert [record.evacuee_count for record in snapshot.records] == [54, 60]


def test_parser_keeps_support_for_plain_weekday_text() -> None:
    snapshot = parse_uki_html(
        _page("9月22日(火曜日)22時00分時点"),
        "https://example.invalid/uki",
    )

    assert snapshot.observed_at_jst == "2026-09-22T22:00+09:00"
