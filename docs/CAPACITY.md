# 熊本防災マップの避難所定員

避難所定員は日次変動する情報ではなく、熊本防災マップの避難所詳細から一度取得し、固定属性マスタとして永続保存します。

## 情報源

定員は「防災情報くまもと」の地図上で避難所を選択した際に表示される詳細情報から取得します。

国土地理院CSVの`受入対象者`とは別の項目です。

- `reference_accepted_persons`: 国土地理院CSVの受入対象者
- `portal_capacity_persons`: 熊本防災マップに表示された定員

## 永続マスタ

```text
reference/portal_shelter_capacity.csv
```

主な列は次のとおりです。

| 列 | 内容 |
|---|---|
| `portal_shelter_id` | 熊本防災側または取得時に生成した避難所ID |
| `municipality_code` | 市町村コード |
| `municipality` | 市町村名 |
| `shelter_name` | 避難所名 |
| `address` | 住所 |
| `portal_capacity_persons` | 数値化した定員 |
| `portal_capacity_raw` | 表示された定員の原文 |
| `capacity_source` | `kumamoto_portal_map` |
| `capacity_acquired_at_jst` | 初回取得日時 |
| `capacity_parse_status` | `parsed`、`missing`、`invalid` |

取得時点のバックアップは次に保存します。

```text
reference/capacity_history/YYYY-MM-DD.csv
```

## 一度だけの取得

GitHub Actionsの`Collect Kumamoto shelter capacity`を使用します。

```text
.github/workflows/collect_capacity.yml
```

処理は次の順序です。

1. 熊本県内の市町村ごとに「全ての避難所」を表示する
2. XHR・Fetchレスポンスから定員を持つ避難所レコードを探索する
3. レスポンスから取得できない場合は、仮想スクロール表の各行をクリックする
4. ポップアップの定員表示を数値化する
5. 既存マスタと統合し、取得済みの定員を保持する
6. 全日次CSVと横持ち時系列CSVへ定員を付与する

通常の日次ワークフローでは地図上の避難所をクリックしません。

## 日次データへの結合

日次避難所収集が完了すると、`Enrich shelter data with capacity`が自動実行されます。

```text
.github/workflows/enrich_capacity.yml
```

結合の優先順位は次のとおりです。

1. 熊本防災側の避難所ID
2. 市町村・正規化施設名・正規化住所の完全一致
3. 市町村・施設名一致と住所の類似一致
4. 高信頼度の類似照合
5. 一致しない場合は空欄として監査対象にする

次の列が日別・最新・横持ちCSVへ追加されます。

```text
portal_shelter_id
portal_capacity_persons
portal_capacity_raw
capacity_source
capacity_acquired_at_jst
capacity_match_status
capacity_match_method
capacity_match_score
portal_latitude
portal_longitude
```

照合できなかった施設は次に保存されます。

```text
data/latest_capacity_matching_issues.csv
```

## 更新

定員は基本的に固定属性として扱います。次の場合のみ定員取得ワークフローを再実行します。

- 新しい避難所が追加された場合
- 定員が変更されたことを確認した場合
- 定員未取得施設だけを再確認する場合
- 熊本防災マップのデータ構造が変更された場合

再取得時も、既存の正常な定員は新しい取得結果が欠損の場合には上書きしません。
