# 自治体公表の避難所別避難者数

八代市と宇城市が公表する避難所別避難者数を収集し、既存の`status_by_date.csv`とは別の横持ちCSVとして保存します。

## 対象情報

世帯数は収集しません。収集対象は避難者数のみです。

| 自治体 | 情報源 | 形式 |
|---|---|---|
| 八代市 | `https://www.city.yatsushiro.lg.jp/kiji00326798/index.html`からリンクされる最新PDF | PDF表 |
| 宇城市 | `https://www.city.uki.kumamoto.jp/kurashi/bosaiinfo/2610320` | HTML表 |

## 横持ちCSV

```text
data/evacuee_count_by_date.csv
```

固定属性は`status_by_date.csv`と同じ11列です。

```text
shelter_id
reference_common_ids
municipality
shelter_name
address
reference_same_address_as_emergency_site
reference_other_mayor_matters
reference_accepted_persons
portal_capacity_persons
latitude
longitude
```

その右側へ日付列を古い順に追加します。

```text
...,2026-07-29,2026-07-30,2026-07-31,2026-08-01,...
```

セルの意味は次のとおりです。

- 数値: 自治体がその避難所について公表した避難者数
- `0`: 自治体が明示的に0人と公表
- 空欄: 自治体情報で値が公表されていない。0人を意味しない

`status_by_date.csv`の開設・混雑状況は変更しません。

## 監査用の縦持ちCSV

```text
data/municipal_evacuees/all_observations.csv
```

1行が1避難所・1観測時刻・1リビジョンです。主な項目は次のとおりです。

```text
municipality
source_observed_at_jst
source_observed_date
retrieved_at_jst
source_shelter_name
source_address
evacuee_count
shelter_id
match_status
match_method
match_score
source_format
source_page_url
source_document_url
raw_sha256
normalized_sha256
revision
```

同じ観測時刻の資料が自治体によって差し替えられた場合は、既存行を削除せず`revision`を増やして保存します。横持ちCSVには同じ日付の最新リビジョンを反映します。

## その他の出力

| ファイル | 内容 |
|---|---|
| `data/municipal_evacuees/latest_observations.csv` | 自治体ごとの最新観測 |
| `data/municipal_evacuees/matching_issues.csv` | 未一致・曖昧な施設と上位候補 |
| `data/logs/municipal_evacuee_collection.json` | 最新の成功時検証結果 |
| `data/logs/municipal_evacuee_last_run.log` | 最新実行ログ |
| `data/logs/municipal_evacuee_last_failure.log` | 最新失敗ログ |

## 更新判定

更新判定には自治体公表時刻と正規化ハッシュを使用します。

| 状態 | 判定 |
|---|---|
| 観測時刻と正規化ハッシュが同一 | 変更なし。新しい観測を追加しない |
| 観測時刻が新しい | 新規観測として追加 |
| 観測時刻は同じで正規化ハッシュが異なる | 訂正としてリビジョン追加 |
| ページやPDFを取得できない | 0人にせずワークフロー失敗 |
| 表の合計と各避難所の合計が不一致 | ワークフロー失敗 |
| 未一致・曖昧な施設が存在 | 自動付与せずワークフロー失敗 |

`raw_sha256`はHTMLまたはPDF全体の変更確認に使用します。`normalized_sha256`は施設名、住所、避難者数を正規化した表の内容変更に使用します。

## 施設照合

次の順序で`status_by_date.csv`の`shelter_id`へ照合します。

1. 手動エイリアス
2. 自治体・正規化施設名・正規化住所の一致
3. 住所一致と施設名類似
4. 施設名一致
5. 高信頼度の類似照合

同じ施設についてWeb由来行と国土地理院由来行が併存する場合は、観測日の開設状態を補助情報として使用します。複数候補を安全に一意化できない場合は、自動確定しません。

手動エイリアスは次に保存します。

```text
reference/municipal_evacuee_shelter_aliases.csv
```

PDFの埋込フォントにより施設名・住所が文字化けまたは欠落する場合も、確認済みの対応関係をこのエイリアスに保存します。

## 自治体別の解析

### 八代市

1. 案内ページから最新の避難所PDFリンクを抽出
2. PDFを取得
3. PyMuPDFで罫線付き表を検出
4. `No.`、避難所名、住所、避難者数を列位置で取得
5. PDFの合計避難者数と各行の合計を照合

文字列の空白位置には依存せず、表の列構造を使用します。

### 宇城市

1. HTMLを取得
2. `避難所名`、`住所`、`避難者数`を持つ表を検出
3. 各避難所の人数を取得
4. 合計行と各行の合計を照合

## 自動実行

```text
.github/workflows/collect_municipal_evacuees.yml
```

自治体ページが8時頃に更新される運用を考慮し、日本時間の毎日8時10分と8時30分に2回確認します。0時05分の日次避難所収集との直接連動は行いませんが、8時10分までには当日の`status_by_date.csv`が更新済みであることを前提に、最新の施設一覧を使って照合します。手動実行も可能です。

GitHub ActionsのcronはUTCで指定するため、ワークフローでは次を設定しています。

```yaml
schedule:
  - cron: "10 23 * * *"  # 翌日08:10 JST
  - cron: "30 23 * * *"  # 翌日08:30 JST
```

8時10分の処理が長引いた場合も8時30分分をキャンセルせず、同じ排他グループ内で順番に実行します。

8時10分時点で情報源が更新されていなければ、新しい観測は追加しません。8時30分に再確認し、自治体公表時刻または正規化ハッシュが変化していれば追加します。2回とも情報源に実質的な変更がなければ、CSVやコミットは追加しません。処理失敗時はHTML、PDF、実行ログをGitHub Actions artifactへ保存します。

## 初回検証結果

2026年8月3日の初回成功時には次を確認しました。

| 自治体 | 観測時刻 | 施設数 | 避難者数合計 | 照合 |
|---|---|---:|---:|---:|
| 八代市 | 2026-08-03 06:00 JST | 43 | 2,948 | 43/43 |
| 宇城市 | 2026-08-03 08:00 JST | 11 | 3,248 | 11/11 |

未一致・曖昧施設は0件でした。

2026年8月3日18時の八代市PDFでは、PDF埋込フォントにより「今泉地区公民館」が「今晋地区公⺠館」等として抽出されました。確認済みの手動エイリアスを追加し、43施設すべてを一意に照合しています。
