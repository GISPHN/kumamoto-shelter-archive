# 八代市の欠測日回収（2026年9月8日）

`yatsushiro_20260805_20260807.csv` は、八代市の2026年8月5日12時と8月7日12時（日本時間）の避難者数の転記表です。両日41施設で、合計はそれぞれ2,462人、2,174人です。東京大学沼田研究室の施設別表から抽出し、同サイトが保存する原資料画像の人数欄と照合しました。

- 公開ページ: https://www.numa.iis.u-tokyo.ac.jp/yatsushiro/shelter_map.php
- 8月5日原画像: https://www.numa.iis.u-tokyo.ac.jp/yatsushiro/shelter_history_sources/2026-08-05.png
- 8月7日原画像: https://www.numa.iis.u-tokyo.ac.jp/yatsushiro/shelter_history_sources/2026-08-07.png

施設名は公開ダッシュボードの表記です。既存の照合処理で施設IDを付けています。掲載された0人は明示的な0として登録し、掲載のない施設には値を付けていません。世帯数は対象外です。

## 監査用CSVへの記録

`data/municipal_evacuees/all_observations.csv` に82件を追加し、`data/evacuee_count_by_date.csv` の対応する欠測82セルへ反映しています。既存の観測行・非空欄値・施設固定属性と宇城市のデータは保持しています。最新観測ファイルは更新対象ではありません。

- `source_format`: `csv_transcription`（保存した原画像から確認した転記表）
- `source_document_url`: 各日の原画像URL
- `source_page_url`: 公開ダッシュボードURL
- `raw_sha256`: このディレクトリの転記CSVの実バイト列（UTF-8 BOM、CRLF）のSHA-256。原画像のハッシュではありません。
- `normalized_sha256`: 既存の `normalized_snapshot_hash` による各日の施設名・人数のハッシュ
- `source_observed_at_jst`: 各日12時
- `retrieved_at_jst`: 回収値を今回取り込んだ時刻
- `revision`: 1（該当観測日時の既存登録なし）

元の八代市公開PDFを再取得した記録とは区別し、二次保存された原資料からの回収であることを明示しています。
