# 熊本防災ポータルの避難所定員

避難所の最大収容人数は日次変動する開設状況とは分離し、熊本防災ポータルの固定施設属性として永続保存します。

## 取得元

熊本防災ポータルが地図マーカーの生成に使用する、全県避難所JSONを直接取得します。

```text
https://portal.bousai.pref.kumamoto.jp/data/shelter/shelter.json
```

このJSONはトップレベルに`items`と`total`を持ち、`items`の各要素が1避難所です。2026年7月31日の検証時点では、45市町村、2,122施設が収録されていました。

主なフィールドは次のとおりです。

| JSONフィールド | 保存先 | 内容 |
|---|---|---|
| `facilityId` | `portal_shelter_id` | 熊本防災ポータル側の施設ID |
| `municipalityCd` | `municipality_code` | 市町村コード |
| `municipalityName` | `municipality` | 市町村名 |
| `name` | `shelter_name` | 避難所名 |
| `address` | `address` | 住所 |
| `latitude` | `portal_latitude` | ポータル側の緯度 |
| `longitude` | `portal_longitude` | ポータル側の経度 |
| `capacity` | `portal_capacity_persons` | 最大収容人数 |

地図上のポップアップでは、この`capacity`が「最大収容人数」として表示されます。

## 国土地理院データとの区別

国土地理院CSVの`受入対象者`と、熊本防災ポータルの最大収容人数は別項目です。

| 列 | 情報源 | 意味 |
|---|---|---|
| `reference_accepted_persons` | 国土地理院CSV | 高齢者、障害者等の受入対象者 |
| `portal_capacity_persons` | 熊本防災ポータルJSON | 最大収容人数 |

`reference_accepted_persons`を人数として解釈したり、定員へ転記したりしません。

## 永続マスタ

取得結果は次に保存します。

```text
reference/portal_shelter_capacity.csv
reference/portal_shelter_capacity_metadata.json
```

取得時点の履歴は次に保存します。

```text
reference/capacity_history/YYYY-MM-DD.csv
reference/capacity_history/YYYY-MM-DD.metadata.json
```

CSVの主な列は次のとおりです。

| 列 | 内容 |
|---|---|
| `portal_shelter_id` | `facilityId` |
| `municipality_code` | 市町村コード |
| `municipality` | 市町村名 |
| `shelter_name` | 避難所名 |
| `address` | 住所 |
| `portal_latitude` | 緯度 |
| `portal_longitude` | 経度 |
| `portal_capacity_persons` | 正の数として確認できた最大収容人数 |
| `portal_capacity_raw` | JSONの`capacity`原値 |
| `capacity_source` | `kumamoto_portal_map` |
| `capacity_acquired_at_jst` | 取得日時 |
| `capacity_match_key` | 日次データとの結合用キー |
| `capacity_parse_status` | 定員値の解釈結果 |
| `source_url` | 取得元JSON |

## `capacity`が0の場合

ポータルのポップアップでは、`capacity == 0`が人数の0ではなく`---`として表示されます。

そのため、次のように保存します。

```text
portal_capacity_persons = 空欄
portal_capacity_raw = 0
capacity_parse_status = missing_zero
```

「収容可能人数が0人」とは解釈しません。

2026年7月31日の取得結果は次のとおりです。

| 区分 | 施設数 |
|---|---:|
| 全施設 | 2,122 |
| 正の最大収容人数を取得 | 1,619 |
| `capacity=0`でポップアップが`---` | 398 |
| `capacity`が欠損 | 105 |
| 解析不能な値 | 0 |

全2,122施設のレコードは取得できていますが、数値として利用できる最大収容人数は1,619施設です。

## 取得処理

専用ワークフローを使用します。

```text
.github/workflows/collect_capacity.yml
```

処理は次の順序です。

1. 全県避難所JSONをキャッシュ回避パラメータ付きで取得する
2. HTTPステータス、Content-Type、レスポンス件数を確認する
3. 必須フィールドの欠落を検査する
4. `facilityId`の空欄・重複を検査する
5. `capacity`を正の数、0、欠損、不正値へ分類する
6. 永続マスタと取得履歴を保存する
7. 既存の日別・最新・横持ちCSVへ定員を結合する
8. 生成後のマスタ件数と横持ちCSVの列を再検証する

地図マーカーや一覧行をクリックする処理は使用しません。全県JSONを1回取得するため、市町村ごとの巡回も不要です。

## 日次データへの結合

日次の避難所収集後は、保存済みの定員マスタを読み込むだけです。定員取得元へ毎日アクセスする必要はありません。

```text
.github/workflows/enrich_capacity.yml
```

結合の優先順位は次のとおりです。

1. 熊本防災ポータル側の施設ID
2. 市町村・正規化施設名・正規化住所の完全一致
3. 市町村・施設名一致と住所の類似一致
4. 高信頼度の類似照合
5. 一致しない場合は未一致として保存

追加される列は次のとおりです。

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

未一致・曖昧な施設は次に保存します。

```text
data/latest_capacity_matching_issues.csv
```

## 検証情報

最新の取得検証結果は次に保存します。

```text
reference/portal_shelter_capacity_metadata.json
data/logs/capacity_latest_run.log
```

メタデータには、取得日時、レスポンスサイズ、SHA-256、全施設数、定員取得件数、市町村数、必須フィールド欠落件数を記録します。

## 再取得する場合

定員は固定属性として扱います。次の場合に限り、`Collect Kumamoto shelter capacity`を再実行します。

- 新しい避難所が追加された場合
- 最大収容人数が変更された場合
- 欠損施設の値が追加された場合
- ポータルJSONの構造が変更された場合

再取得時に、以前は正の定員が登録されていた施設の値が一時的に欠損した場合、直前の検証済み定員を保持します。その事実は`capacity_parse_status=preserved_previous_parsed`で明示します。
