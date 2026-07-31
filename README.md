# 熊本県避難所日次アーカイブ

熊本県の「防災情報くまもと」に掲載される避難所情報を毎日自動取得し、CSVとして蓄積するリポジトリです。

開設状況・混雑状況はJavaScriptで描画された避難所一覧をPlaywrightで収集します。施設属性には国土地理院の指定緊急避難場所・指定避難所CSVを使用し、最大収容人数には熊本防災ポータルが地図表示に使用する全県避難所JSONを使用します。

## 目的

- 避難所の開設、未開設、閉鎖への変化を日単位で記録する
- 開設中の混雑状況を継続的に保存する
- 避難所ごとの開設日数、連続開設日数、混雑状態の推移を分析できる形にする
- 共通ID、住所、座標、受入対象者、最大収容人数等を付与する
- 縦持ち形式と横持ち形式の両方を保存する

## データ取得元

### 開設状況・混雑状況

防災情報くまもとの公開Webページを使用します。

```text
https://portal.bousai.pref.kumamoto.jp/sp.html?p=evacuation%2Fshelter
```

避難所一覧はDojo dgridによる仮想スクロール形式です。通常のHTML表抽出では全行を取得できないため、Playwrightでスクロール領域を移動し、表示された行を施設ID単位で重複除去しながら収集します。

### 国土地理院の施設属性

国土地理院「指定緊急避難場所・指定避難所データ」熊本県CSVを使用します。

```text
https://hinanmap.gsi.go.jp/hinanjocp/defaultFtpData/csv/43000_1.csv
```

主な付与項目は次のとおりです。

- 共通ID
- 施設・場所名
- 住所
- 緯度・経度
- 指定緊急避難場所との住所同一
- その他市町村長が必要と認める事項
- 受入対象者
- 備考

### 最大収容人数

熊本防災ポータルが地図マーカーの生成に使用する全県避難所JSONを使用します。

```text
https://portal.bousai.pref.kumamoto.jp/data/shelter/shelter.json
```

JSONの`capacity`が、地図上のポップアップに表示される「最大収容人数」です。市町村ごとの巡回やマーカークリックは行わず、全県分を1回で取得します。

2026年7月31日の検証結果は次のとおりです。

| 項目 | 件数 |
|---|---:|
| 全避難所レコード | 2,122 |
| 市町村 | 45 |
| 正の最大収容人数を取得 | 1,619 |
| `capacity=0`で画面表示が`---` | 398 |
| `capacity`が欠損 | 105 |
| 解析不能な値 | 0 |

`capacity=0`は0人定員ではなく、ポップアップで`---`と表示される未登録値として扱います。

国土地理院の`受入対象者`と最大収容人数は別項目です。

| 列 | 意味 |
|---|---|
| `reference_accepted_persons` | 国土地理院CSVの受入対象者 |
| `portal_capacity_persons` | 熊本防災ポータルJSONの最大収容人数 |

詳細は[`docs/CAPACITY.md`](docs/CAPACITY.md)を参照してください。

## 更新時刻

日次避難所収集はGitHub Actionsで日本時間の毎日0時05分に実行します。

```yaml
schedule:
  - cron: "5 0 * * *"
    timezone: "Asia/Tokyo"
```

GitHub Actionsの起動、情報提供元の更新、通信状況には遅延が生じる場合があります。CSVには次の時刻を分けて保存します。

- `snapshot_date_jst`: 記録対象日
- `retrieved_at_jst`: 実際に取得した日本時間
- `source_updated_at_text`: Webページ上の更新時刻表示
- `source_updated_at_jst`: 更新時刻表示をISO形式へ変換した値
- `capacity_acquired_at_jst`: 最大収容人数マスタの取得日時

## 日次状態の作成方法

防災情報くまもとの表は開設中の避難所一覧です。そのため、全施設の日次状態を次の手順で作成します。

1. Webページから開設中の避難所を取得する
2. 国土地理院CSVの施設群を日次データの母集団とする
3. Webページと照合できた施設を開設状態として上書きする
4. Web開設一覧に掲載されなかった施設を未開設として記録する
5. 国土地理院CSVにないWeb側施設も削除せず追加する
6. 保存済み最大収容人数マスタを結合する
7. 前日から状態が変化した施設を差分CSVへ保存する

## 施設照合

### 国土地理院CSVとの照合

施設名、住所、市町村、正規化後の類似文字列を組み合わせ、保守的に照合します。

| `reference_match_status` | 意味 |
|---|---|
| `matched` | 1施設へ一意に照合 |
| `matched_multiple` | 同一施設に複数の共通IDが存在 |
| `ambiguous` | 複数候補があり一意に決められない |
| `unmatched` | 参照CSVに候補がない |

### 最大収容人数マスタとの照合

次の優先順位で結合します。

1. 熊本防災ポータル側の施設ID
2. 市町村・施設名・住所の完全一致
3. 市町村・施設名一致と住所類似
4. 高信頼度の類似照合

結果は`capacity_match_status`、`capacity_match_method`、`capacity_match_score`に保存します。

### 座標が空欄になる場合

- `reference_latitude`・`reference_longitude`: 国土地理院CSVとの照合に成功した場合に付与
- `portal_latitude`・`portal_longitude`: 熊本防災ポータルの定員マスタとの照合に成功した場合に付与

片方の情報源へ照合できない場合でも、もう一方の座標を取得できる場合があります。未一致は推定で補完せず、監査対象として保存します。

## 永続マスタ

| ファイル | 内容 |
|---|---|
| `reference/43000_1.csv` | 国土地理院の熊本県避難所CSV |
| `reference/portal_shelter_capacity.csv` | 熊本防災ポータルの最大収容人数マスタ |
| `reference/portal_shelter_capacity_metadata.json` | 取得件数、SHA-256、欠損状況等の検証情報 |
| `reference/capacity_history/YYYY-MM-DD.csv` | 最大収容人数マスタの取得履歴 |
| `reference/capacity_history/YYYY-MM-DD.metadata.json` | 各取得時点の検証情報 |

## 出力ファイル

### 最新状態

| ファイル | 内容 |
|---|---|
| `data/latest.csv` | 全施設の最新日次状態 |
| `data/latest_open.csv` | 最新時点で開設中の施設のみ |
| `data/latest_changes.csv` | 前日から変化した施設 |
| `data/latest_matching_issues.csv` | 国土地理院CSVとの未一致・曖昧施設 |
| `data/latest_capacity_matching_issues.csv` | 最大収容人数マスタとの未一致・曖昧施設 |

### 日別スナップショット

```text
data/daily/YYYY/MM/YYYY-MM-DD.csv
data/changes/YYYY/MM/YYYY-MM-DD.csv
data/matching_issues/YYYY/MM/YYYY-MM-DD.csv
```

日別CSVは縦持ち形式です。1行が1避難所、1ファイルが1観測日です。

### 全期間の縦持ちデータ

| ファイル | 内容 |
|---|---|
| `data/all_snapshots.csv` | 全日の日別CSVを縦方向に結合 |
| `data/logs/collection_log.csv` | 日次収集の件数、照合率、実行結果 |
| `data/logs/latest_run.log` | 最新の日次収集ログ |
| `data/logs/capacity_latest_run.log` | 最新の定員マスタ取得・検証ログ |

## 横持ち時系列CSV

日次収集後に`scripts/build_time_series.py`が日別CSVを読み込み、施設を行、日付を列とする横持ち形式を再構築します。

### `data/status_by_date.csv`

開設状況と混雑度を1セルにまとめます。

```text
shelter_id,municipality,shelter_name,portal_capacity_persons,...,2026-07-30,2026-07-31
web:001,合志市,須屋市民センター,300,...,開設（平常）,未開設
```

日付列の値は次のいずれかです。

- `未開設`
- `開設（平常）`
- `開設（やや混雑）`
- `開設（混雑）`
- `開設（不明）`
- `状態不明`

### `data/open_status_by_date.csv`

| 値 | 意味 |
|---:|---|
| `1` | 開設 |
| `0` | 未開設 |
| 空欄 | 状態不明 |

### `data/congestion_by_date.csv`

混雑状態を日付列へ保存します。

- `未開設`
- `平常`
- `やや混雑`
- `混雑`
- `不明`
- `状態不明`

### 横持ちCSVの固定属性

日付列の左側には、主に次を保持します。

- 避難所ID、市町村、施設名、住所
- 国土地理院の共通ID・座標・受入対象者・備考
- 熊本防災ポータルの施設ID・最大収容人数・座標
- 各情報源との照合状態、照合方法、照合スコア
- 定員マスタの取得日時

## 差分の種類

| `change_type` | 意味 |
|---|---|
| `new` | 前回データに存在しない施設 |
| `opened` | 未開設から開設へ変化 |
| `closed` | 開設から未開設へ変化 |
| `opening_status_changed` | 開設状況の表記が変化 |
| `congestion_changed` | 混雑状況が変化 |
| `evacuee_count_changed` | 避難者数が変化 |
| `unchanged` | 前日から変化なし |
| `removed_from_listing` | 前回存在したWeb由来施設が今回存在しない |

最大収容人数は固定属性として扱うため、通常の日次差分判定には含めません。再取得した定員マスタの履歴で変更を確認できます。

## GitHub Actions

| ワークフロー | 用途 |
|---|---|
| `Collect Kumamoto shelter data` | 毎日の開設・混雑状況収集 |
| `Collect Kumamoto shelter capacity` | 全県最大収容人数マスタの初回取得・必要時再取得 |
| `Enrich shelter data with capacity` | 保存済み定員マスタを日別・最新CSVへ結合 |
| `Rebuild shelter time-series CSVs` | 横持ち時系列CSVの独立再構築 |
| `Diagnose Kumamoto shelter capacity source` | ポータル構造変更時の診断 |

## 手動実行

日次データを取得する場合:

1. `Actions`を開く
2. `Collect Kumamoto shelter data`を選択する
3. `Run workflow`を押す
4. 必要に応じて記録日を入力する

最大収容人数を再取得する場合:

1. `Actions`を開く
2. `Collect Kumamoto shelter capacity`を選択する
3. `Run workflow`を押す

最大収容人数は毎日再取得する必要はありません。

## ローカル実行

Python 3.12を想定しています。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium

python scripts/collect_capacity.py
python scripts/enrich_capacity_outputs.py --data-root data
python scripts/collect_shelters.py --snapshot-date 2026-07-31
python scripts/build_time_series.py --data-root data
```

Windows PowerShellでは仮想環境を次のように有効化します。

```powershell
.\.venv\Scripts\Activate.ps1
```

## 主な構成

```text
.github/workflows/collect.yml
.github/workflows/collect_capacity.yml
.github/workflows/enrich_capacity.yml
.github/workflows/rebuild_time_series.yml
scripts/collect_shelters.py
scripts/reference_matcher.py
scripts/collect_capacity.py
scripts/capacity_matcher.py
scripts/enrich_capacity_outputs.py
scripts/build_time_series.py
reference/
data/
docs/CAPACITY.md
README.md
```

## 利用上の注意

- WebページまたはJSONの構造変更により取得処理が停止する可能性があります。
- 行政機関による入力から公開情報への反映までに時間差が生じる場合があります。
- GitHub Actionsは指定時刻から遅れて開始される場合があります。
- 最大収容人数が空欄の施設は、元JSONで0または欠損であり、自動推定していません。
- 研究や災害対応に利用する場合は、情報源、取得日時、照合状態を確認してください。
- 公開情報の保存、加工、再配布に関する利用条件は、各情報提供元の規約を確認してください。

## 開発者

GISPHN / Ryo Horiike
