# 実装計画: 開始の拍子・テンポの自律的な読み取り

解析が「拍子と小節を理解するのに必要な情報(拍子・テンポ)が足りているか」を自分で確かめ、
足りなければ**追加の画像処理**か**人に聞く**かを自分で選ぶ。`docs/todo.md` 段階3
「拍子が画像から失われるケースへの耐性」に対応する。

## 背景

`tmp/ダンス２楽章.jpeg`(Dance Movements 2楽章, 3rd Bb Cl, 練習番号28〜)で指揮が
「4拍振り + 速度半分」になった。曲の**途中のページ**なので拍子記号もテンポ表示も前のページにあり、
開始の拍子もテンポも取れないまま 4/4・♩=136 に化けていた。正解は **2/2・𝅗𝅥=134**(= 4分換算 ♩=268)。

## 方針・アーキテクチャ

### 1. 読む前に「読めるか」を確かめる(triage)

本読み(`read_meter.py`)の前に、**開始の拍子・テンポが読めるかだけを判定する軽い段**を置く。

読んでから気づく形(全小節読んで `unknown` が出たら対処)にはしない。理由は**拍子が
carry-forward で後続に伝播する**こと。1小節目を 4/4 と誤ると、そこから先の小節がすべて
その誤りを引き継ぐ。本読みの出力が丸ごと汚染されるので、本読みを始める前に決着させる。

triage が安いのは、**見る範囲が狭いから**。開始の拍子・テンポ・タイトルはページの先頭
(1段目の左上)にしかない。全段(10枚)を渡す本読みに対し、triage は**ページ頭の1枚だけ**で済む。
画像入力がコストの大半を占めるので、追加コストは本読みの1割程度。

### 2. 自律のはしご(エスカレーション)

安い手から順に試し、**人に聞くのは最後**。

```
① triage: ページ頭を広く切り出して「拍子・テンポが読めるか」を見る   ← 安い
      ↓ 読めない
② さらなる画像処理: 足りない部分を拡大して見直す(1回だけ)          ← 自律
      ↓ それでも読めない
③ 人に聞く: 読めなかった項目だけ、理由を添えて聞く                 ← 最後
      ↓
本読み(read_meter.py)に、確定した開始情報をヒントとして渡す
```

①②はサーバ内で完結する(API に出さない)ので、人から見れば「くまが勝手に頑張ってから聞いてくる」。

### 3. ズームが効くケースと効かないケースを区別する

②を無条件のリトライにしない。**ズームで直るのは「あるのに読めない」ときだけ**で、
「そこに無い」ときは何回拡大しても出てこない。triage に状態を区別して報告させる。

| 状態 | 意味 | 次の手 |
|---|---|---|
| `printed` | 読めた(拍子記号、または ♩=134 のような数値) | ①で完了 |
| `text_only` | テンポのみ。`Andante` のような**速度標語だけで数値が無い** | 表で引けたら完了、引けなければ**③人に聞く** |
| `unreadable` | ありそうだが読めない(端で切れている・小さい・ぼけ) | **②拡大**が効く |
| `absent` | そこに印刷されていない | ②は無意味 → **③人に聞く** |

#### 判定の軸は「1番最初の小節に書いてあるか」だけ

**拍子もテンポも、曲の1番最初の小節に必ず記載がある**(楽譜の決まり)。
だから見るべきはそこ1箇所で、**無ければ人に聞くしかない**。

> NOTE: 「記載が無い＝2枚目の写真」とは**判断しない**。1枚目でも3枚目でも、
> 最初の小節に記載が無いことはある。判断するのは「読めたか/読めなかったか」だけ。

曲名・作曲者・練習番号は判断に使わない。

> NOTE: **パート名(「3rd Bb Clarinet」等)は特に使えない。** 毎ページの上部に繰り返し
> 印刷される(running header)ため、継続ページにも出る。`tmp/ダンス２楽章.jpeg` で確認済み。

#### 速度標語(`text_only`)の扱い

テンポの表記には2通りある。

1. **数値**: `♩=134` / `𝅗𝅥=72`。そのまま使える → `printed`
2. **速度標語**: `Andante` / `Allegro`。表で数値に変換する → `text_only`

標語は `spike/tempo.py` の `TEMPO_WORDS`(高校音楽の教科書の表)で**確定値に変換する**。
**表に無い標語は人に聞く**(勝手に推測しない)。

> TODO: 表の数値は1点のみで、時代・作曲家による違いを持っていない
> (バロックの `Allegro` はロマン派より遅い)。当面は確定値として使い、調整は後続とする。

> NOTE: 標語→数値の変換は**AIにさせずコード側で表を引く**(`TEMPO_WORDS`)。
> AIには標語を**文字のまま**返させる。決定的で検証でき、AIが出典不明の数値を作らないため。

### 4. 「もっと大きく見る」には具体的な仕事がある

既存の段画像(`clean_NN.jpg`)は**テンポ表示を落としている可能性が高い**。
`staves.py:151` の切り出しは段の上マージンが `space * 4`(五線1つぶんの高さ)しかなく、
テンポ表示・タイトル・作曲者はその上に印刷されるため、入りきらない。

```
  DANCE MOVEMENTS        ← 切れている(タイトル)
  𝅗𝅥 = 134                ← 切れているかもしれない(テンポ表示) ← ①で取りに行く対象
┌─────────────────┐   ← clean_00.jpg の上端(space*4)
│ 𝄞 ♯♯ 2/2  ♪♪♪♪  │
└─────────────────┘
```

> NOTE: 左マージンが狭い(`space*2`)のも意図的(クレフ・調号を小節線検出から除外するため)。
> 検出用の狭いクロップは変えない。**triage は元写真から自前に切り出す**。
> これは `docs/todo.md`「AIに渡す画像だけ広くする(decouple)」の最初の実装になる。

だから2つのパスは「広げる」と「拡大する」で役割が違う:

| パス | 切り出し | 狙う失敗 |
|---|---|---|
| ① head | **写真の上端から1段目の下端まで**(幅は写真の全幅。元解像度のまま) | 端で切れて写っていない |
| ② zoom | 1段目の左端側(拍子記号・テンポ表示のある範囲)を 2倍に拡大 | 小さくて読めない・ぼけ |

> NOTE: ①で「上に `space*N`」のような定数を使わない。テンポ表示が五線の上どれだけに置かれるかは
> 楽譜ごとに違い、写真の余白の取り方でも変わるので、1枚に合わせた定数は次の写真で外れる。
> **1段目より上は定義上まるごとページのヘッダ**(曲名・作曲者・パート名・テンポ・小節番号)なので、
> 全部取れば調整値が要らない。余分に入るのは白紙の余白だけで害がない。

### 5. 出処(source)は本読みの保険として残す

triage で開始情報を確定しても、本読みが曲の**途中**で拍子を取りこぼすことはある。
各小節の `ts_source` は本読み側に残し、紫破線「要確認」の導線につなぐ。

#### なぜ confidence を使わないか

既存の `needStartConfirm()` は `confidence < 1` を条件にしているが、これは機能しない。

* `confidence` は self-consistency の多数決率(`cnt / runs`, `read_meter.py:147`)
* `METER_RUNS` はリポジトリのどこにも設定が無く既定 `1` → **confidence は常に 1.0** で発火しない
* 仮に `METER_RUNS=3` にしても今回のケースは救えない。「AIが迷った」のではなく
  「画像に情報が無い」ので、3回とも自信満々に同じ 4/4 を答えて一致する

→ **信頼度は「わからない」の指標として原理的に使えない。** 出処を別の軸で持つ。

| 軸 | フィールド | 意味 | 使い道 |
|---|---|---|---|
| ぶれ | `confidence` | 同じ画像を複数回読んで一致したか | 紫破線「要確認」(既存のまま) |
| 不在 | `state` / `ts_source` | その情報が画像にあったか | **はしごの分岐(今回)** |

### 6. テンポも拍子も小節ごとに持つ

`docs/spec.md`「テンポ: 曲頭の `tempo` のみ」を改める。拍子と同じく曲中で変わるため小節ごとに持つ。

ただし**テンポは「書かれている小節だけ」に持つ(anchor 方式)**。拍子のように全小節へ展開しない。

* テンポ表示は1曲に数個しかない。60小節に複製すると人が1点直したときの伝播漏れ(desync)が起きる
* anchor なら「1点を置く/外す」だけ。引き継ぎ(carry-forward)はフロントの `measureTempos` が担う

## ファイル構成

* `spike/tempo.py` — **新規**: テンポの知識を1箇所に集める。音符単位の4分換算と、
  速度標語(`Andante` 等)のBPM目安範囲。`read_start.py` と `read_meter.py` が共有
* `spike/read_start.py` — **新規**: triage(①)＋自律的な拡大再読(②)。開始情報と「人に聞くべきか」を出す
* `spike/detect_measures.py` — 変更: `space`(五線のすき間)を `staves.json` に残す
* `spike/read_meter.py` — 変更: テンポ表示の読み取り、出処の申告、開始情報ヒントの取り込み
* `spike/server.py` — 変更: `/process` を `/process/detect`(段階1 + triage)と `/process/read/{job}` に分割
* `web/index.html` — 変更: 2段解析の呼び出し、読めなかった項目だけ聞くポップアップ、小節ごとのテンポ
* `docs/pipeline.md` — 変更: triage 段の追加、2段構成、テンポは小節ごと、self-consistency の実態(既定1回)
* `docs/spec.md` — 変更: 段階3のフィールド方針(テンポは曲頭のみ → 小節ごと、state/source の追加)
* `docs/todo.md` — 変更: 段階3「画像耐性」の進捗。decouple の第一歩が入ったこと

## クラス・型定義

### `spike/tempo.py`(新規)

* **役割**: テンポ表示の単位を4分音符換算に正規化する。`read_start.py` と `read_meter.py` が共有
  (`barlines.py` / `staves.py` と同じ、ローカルモジュールを import する既存の作りに合わせる)

* `UNIT_FACTOR: dict[str, float]` — 音符単位 → 4分音符換算の係数
  `{"quarter":1, "half":2, "eighth":0.5, "dotted_quarter":1.5, "dotted_half":3, "whole":4}`
  > NOTE: 𝅗𝅥=134 は ♩=268 と同じ速さ(2分音符1つ = 4分音符2つ)。フロントのタイムラインは
  > すべて4分換算で動いているので、ここで正規化して吸収する。

* `quarter_bpm(value, unit) -> int | None` — `value * UNIT_FACTOR[unit]` を丸めて4分換算BPMに。
  未知の `unit` や非現実的な値(20未満・400超)は `None`(読めなかった扱い)

* `TEMPO_WORDS: dict[str, int]` — 速度標語 → ♩のBPM(確定値)。出典は高校音楽の教科書の表

  | 標語 | BPM | 意味 | 標語 | BPM | 意味 |
  |---|---|---|---|---|---|
  | `grave` | 40 | 重々しくゆるやかに | `moderato` | 96 | 中ぐらいの速さで |
  | `largo` | 46 | 幅広くゆるやかに | `allegretto` | 108 | やや速く |
  | `lento` | 52 | ゆるやかに | `animato` | 120 | 元気に速く |
  | `adagio` | 56 | ゆるやかに | `allegro` | 132 | 速く |
  | `larghetto` | 60 | Largo よりやや速く | `assai` | 144 | — |
  | `adagietto` | 66 | — | `vivace` | 160 | 活発に速く |
  | `andante` | 72 | ゆっくり歩くような速さで | `presto` | 184 | 急速に |
  | `andantino` | 80 | Andante よりやや速く | `prestissimo` | 208 | — |
  | `maestoso` | 88 | 荘厳に | | | |

  > TODO: 1点の値しか持っていない。時代・作曲家による違い(バロックの `allegro` は
  > ロマン派より遅い)の反映は後続。当面はこの値を確定値として使う。

* `word_bpm(text) -> int | None` — 表記から BPM を引く
  1. 小文字化し、記号・括弧を落とす
  2. 完全一致すればその値
  3. 完全一致しなければ、表の標語が**単語として**いくつ含まれるかを数える
     * ちょうど1つ → その値(例 `"Allegro con brio"` → `allegro` = 132)
     * 0個 または 2個以上 → `None`(例 `"Allegro assai"` は `allegro` と `assai` の両方に
       当たるので判断せず人に聞く)
  > NOTE: 表に無い標語・曖昧な標語は**推測せず `None` を返して人に聞く**。
  > 修飾語(`molto` / `poco` / `ma non troppo` 等)で値をずらす補正もしない。

### `spike/read_start.py`(新規)

* **役割**: 開始の拍子・テンポが読めるかを判定し、読めなければ拡大して1回やり直す。
  それでも読めなければ「人に聞く項目」を出力する
* **使い方**: `python read_start.py <out_dir> <元写真>` → `start.json`

* `crop_head(img, staves, space, mode) -> bytes` — ページ頭を切り出す
  * `mode="head"`: 写真の上端から1段目の下端(`crop.y + crop.h`)まで。幅は写真の全幅
  * `mode="zoom"`: 1段目の左端側(拍子記号・テンポ表示のある範囲)を2倍に拡大。
    範囲は `space` の倍数で決める(写真の解像度・撮影距離に依らなくするため)
  > NOTE: 検出用の狭いクロップ(`staves.py`)とは別に、AIに渡す画像だけ広く取る。
  > 段画像 `clean_00.jpg` は上マージン `space*4` でテンポ表示が切れている可能性がある。

* `class ItemRead(BaseModel)` — 拍子/テンポ 1項目の読み取り結果
  * `state: str` — `printed` / `text_only`(テンポのみ) / `unreadable` / `absent`
  * `value: str | None` — 読めた値(拍子は `"2/2"`、テンポは数字)
  * `unit: str | None` — テンポのみ。`UNIT_FACTOR` のキー
  * `word: str | None` — テンポのみ。速度標語を**文字のまま**(例 `"Andante con moto"`)。
    数値への変換はコード側(`tempo.word_bpm`)が行う
  * `text: str | None` — 画像の表記そのまま(例 `"Tempo I 𝅗𝅥=134"`)。人の確認用
  * `note: str` — 読めなかった理由(端で切れている/小さい/印刷が無い 等)

* `class StartTriage(BaseModel)` — AIの回答
  * `first_measure_number: int | None` — 1段目の左に印刷された**小節番号**。無ければ `None`
  * `time_signature: ItemRead`
  * `tempo: ItemRead`

  > NOTE: `first_measure_number` は判定には使わず、🐻の文面を正確にするためだけに読む
  > (「314小節目の拍子がわからないっくま」)。`tmp/ダンス２楽章.jpeg` では 314・325 等が
  > 段の左上に印刷されていることを確認済み。

* `decide(triage, zoomed) -> str` — 次の手を選ぶ。`"proceed"` / `"zoom"` / `"ask"`

  | 拍子・テンポの `state` | 既にズーム済み | 次の手 |
  |---|---|---|
  | 両方 `printed` | — | `proceed` |
  | テンポが `text_only` で `word_bpm()` が引けた | — | `proceed`(表の値を確定値にする) |
  | テンポが `text_only` で `word_bpm()` が `None` | — | `ask`(表に無い標語は推測しない) |
  | どれかが `unreadable` | いいえ | `zoom`(あるのに読めないので拡大が効く) |
  | どれかが `unreadable` | はい | `ask` |
  | どれかが `absent` | — | `ask`(拡大しても出てこない) |

  > NOTE: ズームは1回だけ。同じ判定でループしないよう `zoomed` で止める。

プロンプトの要旨:

1. 渡すのは**ページの先頭**の画像。見るのは**1番最初の小節**(1段目の左端)。
   拍子記号とテンポ表示は、曲の1番最初の小節に必ず印刷される
2. 項目ごとに `state` を答える。**読めたフリをしない**のが要点:
   「見つからないときに 4/4 や ♩=120 と推測しないでください。
   印刷されていないなら `absent`、ありそうだが判読できない(端で切れている・小さすぎる)なら
   `unreadable` と答えてください。」
3. テンポが `Andante` のような**速度標語だけ**で数値が無い場合は `text_only` とし、
   標語を `word` に**文字のまま**入れる。
   **標語をBPMに変換しないでください**(変換はコード側が `TEMPO_WORDS` で行う)
4. 1段目の左に**小節番号**が印刷されていれば `first_measure_number` に入れる。
   無ければ `null`。これは人への説明にだけ使う

### データ形式: `start.json`

```jsonc
{
  "status": "ask",                   // ok | ask
  "first_measure_number": 314,       // 1段目の左の小節番号。無ければ null。🐻の文面用
  "ts": { "value": null, "state": "absent" },
  "tempo": { "value": null, "unit": null, "word": null, "text": null, "state": "absent" },
  "ask": ["ts", "tempo"],            // status=ask のとき、人に聞く項目
  "attempts": [                      // 自律的に何をしたかの記録
    { "pass": "head", "result": "absent" }
  ]
}
```

テンポが速度標語だけで、`TEMPO_WORDS` で引けた場合(聞かずに確定):

```jsonc
"tempo": { "value": 72, "unit": "quarter", "word": "Andante",
           "text": "Andante", "state": "text_only" }
```

> NOTE: `attempts` はデバッグ用でもあるが、🐻 の文面を正直にするためにも使う
> (「大きくして見直したけど、このページには書いてないみたい」)。

### データ形式: `meter_ai.json` / `meter_corrected.json`

各小節に `ts_source` を追加。`tempo` 系は anchor のある小節だけ。

```jsonc
{
  "measures": [
    {
      "index": 0,
      "ts": "2/2",
      "beats": 4.0,
      "beat_groups": [],
      "confidence": 1.0,
      "ts_source": "user",         // 追加: printed | courtesy | carried | unknown | user
      "tempo": 268,                // 追加(任意): 4分換算BPM。テンポ表示がこの小節にある時だけ
      "tempo_text": "𝅗𝅥 = 134",     // 追加(任意): 画像の表記そのまま
      "tempo_source": "user"       // 追加(任意): printed | word(速度標語から変換) | user
    }
  ]
}
```

`ts_source` の値: `printed`(その小節に印刷されていた) / `courtesy`(前段末の予告拍子から) /
`carried`(前の小節から引き継いだ) / `unknown`(手がかり無し) / `user`(人が入力・補正)

### `spike/detect_measures.py`

* 変更: `staves.json` の各要素に `"space": space` を追加(106行目付近の `report.append`)
  > NOTE: `read_start.py` がクロップの大きさを `space` の倍数で決めるため。
  > `staves.json` はリストのまま(`export_overlay.py` が `{r["staff"]: r}` で読むので形は変えない)。

### `spike/read_meter.py`

* `load_start(out_dir) -> dict | None` — `start.json` を読む(無ければ `None`)
* `hint_text(start) -> str` — 確定した開始情報をプロンプトに差し込む文を作る
  * 曲頭: 「この画像は曲の先頭です。1小節目の拍子は 2/2、テンポは 𝅗𝅥=134 と確定しています。」
  * 継続ページ: 「この画像は曲の途中のページです。拍子記号・テンポ表示は前のページにあるため
    この画像には無いかもしれません。1小節目の拍子は 2/2 と確定しています。
    以降は画像で拍子記号が変わった小節から更新してください。」
* `class MeasureMeter(BaseModel)` — 既存に `ts_source: str` を追加
* `class TempoMark(BaseModel)` — 曲中のテンポ変化(2つめ以降のテンポ表示)
  * `index: int` / `value: int` / `unit: str` / `text: str`
* `class Result(BaseModel)` — 既存に `tempos: list[TempoMark]` を追加
* `apply_courtesy(...)` — 既存。引き継いだ小節の `ts_source` を `"courtesy"` に更新する処理を追加
* `apply_start(out, start)` — 確定した開始情報を1小節目の anchor にする(`ts_source`/`tempo_source`)

プロンプトへの追加: 曲中のテンポ表示を `tempos` で報告させる。
拍子の出処(`printed`/`courtesy`/`carried`/`unknown`)を申告させる。推測を禁じる。

> NOTE: テンポの self-consistency は取らない(`beat_groups` と同じく最終 run の値を使う)。
> 既定 `METER_RUNS=1` では差が出ず、テンポ表示は拍子より読み間違いが少ない。

### `spike/server.py`

* `job_data(job)` — 変更: `meter_ai.json` が無ければ `"meter": None`、
  `start.json` があれば `"start"` も返す
  > NOTE: `/process/detect` の時点では meter がまだ無い。現状は無条件に読むので落ちる。
* `POST /process/detect` — 写真を受けてジョブを作り、`detect_measures.py` →
  `export_overlay.py` → `read_start.py`。`{job, image, overlay, meter: null, start}` を返す
  > NOTE: `export_overlay.py` は `measures.json` + `staves.json`(どちらも段階1の出力)だけに
  > 依存するので前段に置ける。
* `POST /process/read/{job}` — ボディに人の回答があれば `start.json` に反映して確定させ、
  `read_meter.py` → `read_beat_groups.py` を実行。`job_data(job)` を返す。`prune_jobs()` はここで呼ぶ

### `web/index.html`

追加する関数:

* `buildTempos()` — `measureTempos: number[]` を作る。各要素は小節の有効テンポ(4分換算BPM)
  * `measureTempos[0]` = ヘッダの開始テンポ(`#tempo`)
  * `measureTempos[i]` = その小節に `tempo` anchor があればそれ、無ければ `measureTempos[i-1]`
  * `computeTimeline()` の先頭で呼ぶ(meter 編集・テンポ変更のたびに作り直される)
* `spbAt(i): number` — `60 / measureTempos[i]`。1拍(4分音符)の秒数
  > NOTE: `beatPhase()` は毎フレーム呼ばれるので、引き継ぎを都度たどらず配列に展開しておく。
* `askStart(start): Promise<answer>` — `start.ask` にある項目だけを表示して聞く。
  `start.attempts` から文面を作る(何を試したか正直に言う)

変更する関数(`$('tempo')` を直接読んでいる箇所を `spbAt(i)` に差し替える):

| 関数 | 行(現状) | 変更内容 |
|---|---|---|
| `computeTimeline()` | 682 | metro 分岐の `acc * spb` 累積をやめ、`t += info.beats * spbAt(i)` の逐次積算にする |
| `buildClicks()` | 744 | 小節ループの中で `spbAt(i)` を取る |
| `beatPhase(t)` | 824 | `spb` を `spbAt(k)` にする |
| `loopRange()` | 776 | 末尾小節の長さを `spbAt(b)` で求める |
| `startSolo(k)` | 1317 | 前拍の間隔を `spbAt(k)`(開始小節のテンポ)にする |
| `updateBear(phase)` | 1002 | カーブ量 `curveK` を `measureTempos[phase.k]` から求める |

* アップロード処理(`1243` 付近) — 1回の `/process` を2段に分ける。
  `/process/detect` → 枠を描画 → `start.status === 'ask'` なら `askStart()` →
  `/process/read/{job}` → 調節画面
* `#startpopup` — 既存を項目別表示に変える(拍子だけ/テンポだけ/両方)。
  `#sp2-note` の文面は下記の規則で作る

#### 🐻 の文面

**「2枚目だね」のような推測を言わない。** 1枚目でも3枚目でも、最初の小節に記載が無いことはある。
言うのは「どこが」「何が」わからないか**だけ**。

| `first_measure_number` | 文面 |
|---|---|
| `null` または `1` | 「**始めの拍子とテンポがわからないっくま**」 |
| `314` | 「**314小節目の拍子とテンポがわからないっくま**」 |

聞く項目(`start.ask`)に応じて「拍子」「テンポ」「拍子とテンポ」を入れ替える。
`start.attempts` にズームが含まれていれば「大きくして見てみたけどわからなかったっくま」を添える。
* `needStartConfirm()` / `recascadeStart()` — **保険として残す**。
  triage を通らない経路(保存済みデータの復元、サンプル)でのみ効く。
  判定は `ts_source` ベースに差し替え、旧フォーマット(`ts_source` が無い)は従来の `confidence < 1` のまま
  > NOTE: 旧データは `tempo` も持たないので新条件をそのまま当てると既存の保存曲すべてで
  > ポップアップが出る。後方互換のため分岐する。
* `initFromData(...)` — 1小節目に `tempo` anchor があればヘッダの開始テンポに反映

## 主要な処理フロー

### アップロードから調節画面まで

```
[写真アップロード]
      ↓
POST /process/detect
  ├ detect_measures.py        枠を作る(段階1, OpenCV)          約2秒
  ├ export_overlay.py         枠を元写真座標へ
  └ read_start.py             ① ページ頭を広く切り出して triage   数秒
                                   ↓ unreadable かつ曲頭らしい
                                 ② 拡大して1回やり直す           数秒
                                   ↓
                                 start.json(status: ok | ask)
      ↓
[枠を描いた自分の楽譜を表示]
      ↓
start.status == "ask" ?
  ├ No(ok)  → そのまま次へ(AIが読めたので何も聞かない)
  └ Yes     → 🐻 読めなかった項目だけ聞く
                「314小節目の拍子とテンポがわからないっくま」
                   拍子 [2]/[2]   テンポ [2分音符]=[134]
      ↓
POST /process/read/{job}
  ├ read_meter.py             確定した開始情報をヒントに全小節の拍子を読む   数十秒
  └ read_beat_groups.py       不規則拍子の分け方
      ↓
[くまのアニメ「解析中」で待つ] → [調節画面]
```

### はしごが止まる位置の例

| 写真 | ① triage | ② zoom | ③ 人に聞く |
|---|---|---|---|
| 曲頭・テンポ表示あり | 両方 `printed` → 完了 | — | — |
| 曲頭・テンポ表示が切れていた | `unreadable` | 拡大して読めた → 完了 | — |
| `Andante` だけ(表にある) | テンポ `text_only` → ♩=72 に変換 → 完了 | — | — |
| `Cantabile` だけ(表に無い) | テンポ `text_only` → 引けない | 飛ばす | テンポだけ聞く |
| **継続ページ(ダンス2楽章)** | 両方 `absent` | 飛ばす | 拍子とテンポを聞く |

### テンポの引き継ぎ(carry-forward)

```
小節:      1      2      3      4      5      6
anchor:  ♩=268   -      -    ♩=120    -      -
         └──────────────┘    └──────────────┘
有効:     268    268    268    120    120    120
```

`measureTempos` がこの「有効」の行。`measureStarts` は小節ごとに違う `spbAt(i)` で積算するので、
曲中でテンポが変わっても小節バー・クリック・指揮が同じ時刻でそろう。

## 考慮事項・決定事項

* **triage を本読みと分けた**。本読みの自己申告で後から気づく形にすると、拍子の carry-forward で
  出力が丸ごと汚染される。ページ頭1枚だけ見る triage は安いので、先に決着させる方が得。
* **ズームの前に `absent` / `unreadable` を区別させた**。無条件リトライだと、継続ページや
  「`Andante` だけでメトロノーム記号が無い曲」で、出てこないものを拡大し続けて時間とコストを捨てる。
* **速度標語→BPM の変換はコード側に置いた**(`spike/tempo.py` の `TEMPO_WORDS`)。
  AIには標語を文字のまま返させる。決定的で検証でき、AIが出典不明の数値を作らない。
  知識が1箇所にまとまるので、値を直したいときもそこだけ見ればよい。
* **表に無い標語は推測しない**。`Cantabile` のような発想標語や、`Allegro assai` のように
  表の語が2つ当たる表記は `None` を返し、人に聞く。
* **🐻 は推測を口にしない**。「2枚目だね」のような当て推量は言わず、
  「どこが」「何が」わからないかだけを言う(方針3の NOTE)。
  そのために小節番号(`first_measure_number`)を読んで「314小節目の」と正確に言う。
* **ズームは1回だけ**。同じ判定で無限に回らないよう `zoomed` フラグで止める。
* **triage は元写真から自前に切り出す**。既存の段画像(`clean_NN.jpg`)は上マージン `space*4` で
  テンポ表示・タイトルが切れている。検出用の狭いクロップ(`staves.py`)は小節線検出の精度のために
  必要なので変えず、AIに渡す画像だけ広げる(`docs/todo.md` の decouple 案の第一歩)。
* **①②はサーバ内で完結させ、API に出さない**。人から見ると「くまが勝手に頑張ってから聞いてくる」
  という体験になる。フロントは `status` と `ask` だけ見ればよい。
* **人に聞くときは項目を絞り、理由を言う**。`start.attempts` を使って「何を試したか」を文面にする。
  読めた項目は聞かない。
* **テンポを全小節に展開せず anchor にした**。拍子は `measurePulses()` が毎小節参照するので
  全小節に持つが、テンポ表示は1曲に数個。複製は人が1点直したときの伝播漏れを生む。
* **4分換算に正規化してから保存する**。フロントのタイムライン・`beats`・`beat_groups` はすべて
  4分音符単位。𝅗𝅥=134 のような単位は `UNIT_FACTOR` で吸収し、フロントには4分換算BPMだけ渡す。
  表記は `text` に残して人の確認に使う。
* **`confidence` は残すが、はしごの判定には使わない**。紫破線の「要確認」導線としては有効。
  ただし `METER_RUNS` 既定1回では常に 1.0 である事実を `docs/pipeline.md` に明記する
  (現状のドキュメントは「self-consistency=3回多数決」と書いてあり実態と違う)。
* **小節の区切り自体は今回の対象外**。区切りは段階1(OpenCV)＋調節画面の✂分割/⇔結合で扱う(既存)。
  triage が担うのは「区切りを理解するのに必要な拍子・テンポ」の側。
* **曲中のテンポ変化の人手補正は今回やらない**。AIが読んだ anchor をそのまま使う。
  調節画面の✏編集に「この小節からテンポ変更」を足すのは後続。
* **`rit.`・`accel.`・フェルマータは対象外**(`docs/spec.md` のスコープのまま)。
  読むのは数字付きのメトロノーム記号だけ。
* **保存済みデータの後方互換**を `ts_source` の有無で見分ける。
