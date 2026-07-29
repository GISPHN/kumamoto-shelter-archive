# 熊本県避難所日次アーカイブ

熊本県の「防災情報くまもと」に掲載される避難所情報を、毎日自動取得してCSVとして蓄積するリポジトリです。

APIではなく、JavaScriptで描画された公開Webページの避難所一覧をPlaywrightで読み取ります。取得した開設状況に、国土地理院の指定緊急避難場所・指定避難所CSVの施設属性を照合して付与します。

## 目的

- 避難所の開設、未開設、閉鎖への変化を日単位で記録する
- 開設中の混雑状況を継続的に保存する
- 避難所ごとの開設日数、連続開設日数、混雑状態の推移を分析できる形にする
- 施設名称、住所、共通ID、緯度経度、受入対象者等を付与する
- 縦持ち形式と横持ち形式の両方を保存する

## 取得元

### 開設状況・混雑状況

防災情報くまもと

- https://portal.bousai.pref.kumamoto.jp/sp.html?p=evacuation%2Fshelter&l=15-0&ll=32.63819999999999%2C130.77610000000004&z=9&municipalityCd=430005

Webページの避難所一覧はDojo dgridによる仮想スクロール形式です。通常のHTML表抽出では全行を取得できないため、スクロール領域を自動移動し、表示された行を重複除去しながら収集します。

### 施設属性・緯度経度

国土地理院「指定緊急避難場所・指定避難所データ」熊本県CSV

- https://hinanmap.gsi.go.jp/hinanjocp/defaultFtpData/csv/43000_1.csv

主な付与項目は次のとおりです。

- 共通ID
- 施設・場所名
- 住所
- 緯度
- 経度
- 指定緊急避難場所との住所同一
- 受入対象者
- 備考

## 更新時刻

GitHub Actionsを日本時間の毎日0時05分に実行します。

```yaml
schedule:
  - cron: "5 0 * * *"
    timezone: "Asia/Tokyo"
```

GitHub Actionsの起動時刻、Webサイトの更新時刻、通信状況には遅延が生じる場合があります。CSVには次の時刻を別々に保存します。

- `snapshot_date_jst`: 記録対象日
- `retrieved_at_jst`: 実際に取得した日本時間
- `source_updated_at_text`: Webページ上に表示された更新時刻
- `source_updated_at_jst`: Webページの更新時刻をISO形式へ変換した値

このデータは厳密なリアルタイム情報ではなく、日次の準リアルタイムスナップショットです。

## 日次状態の作成方法

防災情報くまもとの一覧には、現在開設している避難所が掲載されます。そのため、日次の全施設データは次の手順で作成します。

1. Webページから開設避難所を取得する
2. 国土地理院CSVの施設群を日次データの母集団とする
3. Webページと照合できた施設を「開設」として上書きする
4. Webページの開設一覧に掲載されなかった施設を「未開設」とする
5. 国土地理院CSVに存在しないWeb側施設も削除せず追加する
6. 前日から状態が変化した施設を差分CSVへ保存する

## 施設照合

Webページの避難所名と国土地理院CSVの`施設・場所名`を、名称だけで単純結合せず、次の情報を組み合わせて照合します。

- 施設名
- 住所
- 市町村
- 正規化後の類似文字列

照合結果は`reference_match_status`に保存されます。

| 値 | 意味 |
|---|---|
| `matched` | 1施設へ一意に照合 |
| `matched_multiple` | 同一施設に複数の共通IDが存在 |
| `ambiguous` | 複数候補があり一意に決められない |
| `unmatched` | 参照CSVに候補がない |

### 緯度経度が空欄になる場合

開設状況と緯度経度は別の情報源から取得しています。

- 開設状況・混雑状況: 防災情報くまもとのWeb表
- 緯度・経度: 国土地理院CSV

Web上では開設済みでも、国土地理院CSVへ照合できなかった場合は、`reference_latitude`と`reference_longitude`が空欄になります。その場合は`reference_match_status=unmatched`または`ambiguous`となり、`data/latest_matching_issues.csv`にも出力されます。

空欄は「施設に座標が存在しない」という意味ではなく、「今回使用した参照CSVへ安全に結合できなかった」という意味です。

## 出力ファイル

### 最新状態

| ファイル | 内容 |
|---|---|
| `data/latest.csv` | 全施設の最新日次状態 |
| `data/latest_open.csv` | 最新時点で開設中の施設のみ |
| `data/latest_changes.csv` | 前日から変化した施設 |
| `data/latest_matching_issues.csv` | 未一致または曖昧な施設 |

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
| `data/logs/collection_log.csv` | 取得件数、開設件数、照合率、実行結果 |
| `data/logs/latest_run.log` | 最新実行の詳細ログ |

## 横持ち時系列CSV

日次収集が成功するたびに、`scripts/build_time_series.py`が全日別CSVを読み込み、日付を右側へ追加した横持ち形式を再構築します。

### `data/status_by_date.csv`

施設ごとの開設状況と混雑度を1セルにまとめた主分析用CSVです。

```text
shelter_id,municipality,shelter_name,...,2026-07-30,2026-07-31,2026-08-01
ref:001,合志市,須屋市民センター,...,開設（平常）,未開設,未開設
ref:002,合志市,黒石防災拠点センター,...,開設（平常）,開設（やや混雑）,開設（平常）
```

日付列の値は次のいずれかです。

- `未開設`
- `開設（平常）`
- `開設（やや混雑）`
- `開設（混雑）`
- `開設（不明）`
- `状態不明`

### `data/open_status_by_date.csv`

開設状態を数値で保存します。

| 値 | 意味 |
|---:|---|
| `1` | 開設 |
| `0` | 未開設 |
| 空欄 | 状態不明 |

このファイルは、開設日数、連続開設日数、開設率、市町村別開設施設数などの定量分析に適しています。

### `data/congestion_by_date.csv`

混雑状態を日付列へ保存します。

- `未開設`
- `平常`
- `やや混雑`
- `混雑`
- `不明`
- `状態不明`

## 横持ちCSVの固定属性列

横持ち3ファイルの左側には次の属性列を保持します。

- `shelter_id`
- `web_shelter_id`
- `reference_common_id`
- `reference_common_ids`
- `municipality`
- `shelter_name`
- `address`
- `reference_facility_name`
- `reference_address`
- `reference_latitude`
- `reference_longitude`
- `reference_accepted_persons`
- `reference_match_status`

その右側に`YYYY-MM-DD`形式の日付列が古い順に追加されます。

## 差分の種類

`change_type`には主に次の値が記録されます。

| 値 | 意味 |
|---|---|
| `new` | 前回データに存在しない施設 |
| `opened` | 未開設から開設へ変化 |
| `closed` | 開設から未開設へ変化 |
| `opening_status_changed` | 開設状況の文字列表記が変化 |
| `congestion_changed` | 混雑状況が変化 |
| `evacuee_count_changed` | 避難者数が変化 |
| `unchanged` | 前日から変化なし |
| `removed_from_listing` | 前回存在したWeb由来施設が今回存在しない |

## 手動実行

GitHubのリポジトリ画面から次の手順で実行できます。

1. `Actions`を開く
2. `Collect Kumamoto shelter data`を選択する
3. `Run workflow`を押す
4. 必要に応じて記録日を`YYYY-MM-DD`で入力する

記録日を空欄にした場合は、実行日の日本時間が使用されます。

## ローカル実行

Python 3.12を想定しています。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python scripts/collect_shelters.py --snapshot-date 2026-07-30
python scripts/build_time_series.py --data-root data
```

Windows PowerShellの場合は仮想環境の有効化を次のように変更します。

```powershell
.\.venv\Scripts\Activate.ps1
```

## 主な構成

```text
.github/workflows/collect.yml
scripts/collect_shelters.py
scripts/reference_matcher.py
scripts/build_time_series.py
reference/
data/
debug/
requirements.txt
README.md
```

## 利用上の注意

- Webサイトの構造変更により取得処理が停止する可能性があります。
- 行政機関の更新操作からWeb表示までには時間差が生じる場合があります。
- GitHub Actionsは指定時刻から遅れて開始される場合があります。
- 未一致施設の座標は自動的に推定せず、空欄のまま保存します。
- 研究や災害対応に利用する場合は、元の行政情報と取得時刻を確認してください。
- 公開情報の保存、加工、再配布に関する利用条件は、各情報提供元の規約を確認してください。

## 開発者

GISPHN / Ryo Horiike
