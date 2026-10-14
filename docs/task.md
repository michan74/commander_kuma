# タスクリスト

`docs/design.md` の実装計画をタスクに分解したもの。

## 進捗
- 完了: 13 / 16 タスク

---

## タスク

### バックエンド: 土台

- [x] 1. `space` を `staves.json` に残す
  - 対象: `spike/detect_measures.py`
  - 内容: `report.append({...})`(106行目付近)に `"space": space` を追加する。
    `read_start.py` がクロップの大きさを `space` の倍数で決めるために必要。
    `staves.json` はリストのまま形を変えない(`export_overlay.py` が `{r["staff"]: r for r in ...}` で読む)。
  - 確認: 既存の写真で `/process` を流し、`staves.json` の各要素に `space` が入り、
    `export_overlay.py` が今までどおり動くこと。

- [x] 2. テンポの知識モジュールを作る
  - 対象: `spike/tempo.py`(新規)
  - 内容: テンポに関する知識を1箇所に集める。`barlines.py` / `staves.py` と同じローカルモジュールの作りに合わせる。
    - `UNIT_FACTOR`(quarter=1, half=2, eighth=0.5, dotted_quarter=1.5, dotted_half=3, whole=4)と
      `quarter_bpm(value, unit) -> int | None`。未知の `unit`・20未満・400超は `None`
    - `TEMPO_WORDS`(速度標語 → ♩のBPM **確定値**。design.md の表=高校音楽の教科書の出典)と
      `word_bpm(text) -> int | None`。小文字化・記号除去 → 完全一致 → 無ければ
      表の語が単語として含まれる数を数え、ちょうど1つなら採用、0個か2個以上なら `None`
    - 1点の値しか持たない(時代・作曲家の違いは未反映)ことを TODO に書く
  - 確認: `𝅗𝅥=134 → 268` / `♩.=60 → 90` / `♪=200 → 100`、`unit="x"` や `value=1000` で `None`。
    `"Andante" → 72` / `"Allegro" → 132` / `"Allegro con brio" → 132`(1語だけ当たる) /
    `"Allegro assai" → None`(allegro と assai の2語が当たるので聞く) /
    `"Cantabile" → None` / `"I Ritmico" → None`(表に無いので聞く)。

### バックエンド: triage(①②)

- [x] 3. ページ頭のクロップを作る(AI なし・目視で確認)
  - 対象: `spike/read_start.py`(新規)
  - 内容: `crop_head(img, staves, space, mode)` を実装する。元写真から自前に切り出す。
    - `mode="head"`: 写真の上端から1段目の下端(`crop.y + crop.h`)まで。幅は写真の全幅。**定数を使わない**
    - `mode="zoom"`: 1段目の左端側(拍子記号・テンポ表示のある範囲)を2倍に拡大。
      範囲は `space` の倍数で決める
    この段では JPEG を `out_dir` に書き出すだけにして、AI 呼び出しはまだ入れない。
  - 確認: `tmp/ダンス２楽章.jpeg` と `tmp/dance_movement.jpeg` で切り出して目視する。
    - 曲頭: 曲名・作曲者・テンポ表示・拍子記号が入る(既存の `clean_00.jpg` では**テンポ表示が
      上端で半分に切れていた**。`tmp/crop_demo/` に比較画像あり)
    - 継続: 小節番号・練習番号が入る
    - 回転は無し(元写真のまま)で文字が読めるか。読みにくければ回転の要否をここで判断する

- [x] 4. triage の AI 問い合わせを実装する(1パスのみ)
  - 対象: `spike/read_start.py`
  - 内容: `ItemRead`(`state` = printed/unreadable/absent, `value`, `unit`, `text`, `note`)と
    `StartTriage`(`page_position` = head/middle/unclear, `time_signature`, `tempo`)を定義。
    `head` クロップ1枚を渡して問い合わせる。見るのは**1番最初の小節**(拍子もテンポも必ずそこに記載がある)。
    プロンプトの要点は **「見つからないときに 4/4 や ♩=120 と推測しない」**
    (`absent` / `unreadable` を使わせる)、`Andante` のような速度標語だけなら `text_only` にして
    標語を `word` に**文字のまま**入れる(**BPMへの変換はさせない**。コード側の `TEMPO_WORDS` が行う)、
    1段目の左の**小節番号**を `first_measure_number` に読む(🐻の文面用。判定には使わない)。
    `read_meter.py` と同じリトライ(`errors.ServerError` / `httpx.HTTPError` で指数バックオフ)を入れる。
  - 確認: `tmp/ダンス２楽章.jpeg` で拍子とテンポが `absent` になること(**ここが今回の不具合の核心**)、
    かつ `first_measure_number` に 314 等が入ること。
    `tmp/dance_movement.jpeg` では両方 `printed`(4/4・♩=134)で値が合っていること。

- [x] 5. はしごの判定と自律的な拡大再読を実装する
  - 対象: `spike/read_start.py`
  - 内容: `decide(triage, zoomed) -> "proceed" | "zoom" | "ask"` を design.md の判定表どおりに実装し、
    `zoom` なら `mode="zoom"` で1回だけ読み直す(`zoomed` フラグでループを止める)。
    結果を `start.json` に書く: `status`(ok/ask), `first_measure_number`, `ts`, `tempo`,
    `ask`(聞く項目の配列), `attempts`(何を試したかの記録)。
    テンポは数値なら `tempo.quarter_bpm()`、標語なら `tempo.word_bpm()` で解決し、
    生の表記は `text` に残す。標語が引けなければ `ask` に入れる。
  - 確認: ダンス2楽章 → `status="ask"`, `ask=["ts","tempo"]`, `attempts` に zoom が**入らない**
    (`absent` なので飛ばす)。`unreadable` を作れる写真があれば zoom が1回だけ走ること。

### バックエンド: 本読み(read_meter)

- [x] 6. 曲中のテンポ表示を読む
  - 対象: `spike/read_meter.py`
  - 内容: `TempoMark`(`index`, `value`, `unit`, `text`)を定義し `Result.tempos` に追加。
    プロンプトに「メトロノーム記号(♩=134 等)を見つけたら報告。`Allegro` だけの文字は報告しない」を足す。
    `tempo.quarter_bpm()` で4分換算し、`meter_ai.json` の該当小節に
    `tempo` / `tempo_text` / `tempo_source="printed"` を**anchor として**付ける(全小節に展開しない)。
  - 確認: テンポ変化のある写真で、変化する小節にだけ `tempo` が付くこと。

- [x] 7. 拍子の出処(`ts_source`)を申告させる
  - 対象: `spike/read_meter.py`
  - 内容: `MeasureMeter` に `ts_source: str` を追加し、プロンプトで
    `printed` / `courtesy` / `carried` / `unknown` のどれで決めたかを答えさせる。
    推測の禁止(「画像のどこにも拍子記号が無ければ 4/4 と推測せず `unknown`」)も入れる。
    `apply_courtesy()` で引き継いだ小節は `ts_source="courtesy"` に上書きする。
    `meter_ai.json` の各小節に `ts_source` を出す。
  - 確認: 読める写真で、拍子が変わる小節が `printed`、その後が `carried`、
    段をまたいで引き継いだ小節が `courtesy` になること。

- [x] 8. 確定した開始情報を本読みのヒントに渡す
  - 対象: `spike/read_meter.py`
  - 内容: `load_start(out_dir)` で `start.json` を読み、`hint_text(start)` でプロンプトに差し込む文を作る
    (曲頭 / 継続ページで文面を変える。design.md の文例どおり)。
    `apply_start(out, start)` で1小節目に anchor を確定させる(`ts_source` / `tempo_source` = `"user"`)。
  - 確認: ダンス2楽章で、人の回答(2/2・𝅗𝅥=134)を `start.json` に入れて実行すると、
    1小節目が 2/2・`tempo=268` になり、**後続の小節も 2/2 を引き継ぐ**こと
    (従来は 4/4 に化けていた)。

### バックエンド: API の分割

- [x] 9. `job_data` を meter 不在に耐えさせる
  - 対象: `spike/server.py`
  - 内容: `meter_ai.json` が無ければ `"meter": None` を返す。`start.json` があれば `"start"` も返す。
  - 内容(理由): `/process/detect` の時点では meter がまだ無く、現状は無条件に読むので落ちる。
  - 確認: meter が無いジョブのディレクトリで `job_data()` が例外を投げないこと。

- [x] 10. `/process` を2段に分割する
  - 対象: `spike/server.py`
  - 内容: `POST /process/detect`(`detect_measures.py` → `export_overlay.py` → `read_start.py`。
    `{job, image, overlay, meter: null, start}` を返す)と
    `POST /process/read/{job}`(ボディの回答を `start.json` に反映 → `read_meter.py` →
    `read_beat_groups.py`。`job_data(job)` を返す)に分ける。`prune_jobs()` は後段で呼ぶ。
    既存の `/process` は消す(フロントと同時に差し替える)。
  - 確認: curl で2段を順に叩き、1段目が数秒で枠を返し、2段目が meter を返すこと。
    エラー時に今までどおり `{"error": ...}` を 500 で返すこと。

### フロントエンド

- [x] 11. 2段解析の呼び出しに差し替える
  - 対象: `web/index.html`(アップロード処理・1243行目付近)
  - 内容: `/process/detect` → 枠を描画 → `start.status === 'ask'` なら質問 → `/process/read/{job}`
    の流れにする。待ち画面の文言を2段に合わせる(1段目「小節を検出中」/ 2段目「Geminiが拍子を読み取り中」)。
  - 確認: 読める写真で、質問されずに調節画面まで到達すること。

- [x] 12. 読めなかった項目だけ聞くポップアップにする
  - 対象: `web/index.html`(`#startpopup` / `openStartPopup` / `$('st-ok')`)
  - 内容: `askStart(start)` を追加し、`start.ask` にある項目だけ表示する(拍子だけ/テンポだけ/両方)。
    文面は **推測を言わず「どこが」「何が」わからないかだけ**にする:
    `first_measure_number` が `null`/`1` なら「始めの拍子とテンポがわからないっくま」、
    `314` なら「314小節目の拍子とテンポがわからないっくま」。
    **「2枚目だね」のような当て推量は書かない**(1枚目でも3枚目でも記載が無いことはあるため)。
    `start.attempts` にズームがあれば「大きくして見てみたけどわからなかったっくま」を添える。
    回答は `/process/read/{job}` のボディで送る。
  - 確認: ダンス2楽章で、拍子とテンポの両方を聞くポップアップが出ること。
    テンポだけ `absent` のケースではテンポ欄だけが出ること。

- [ ] 13. 小節ごとのテンポでタイムラインを計算する
  - 対象: `web/index.html`
  - 内容: `buildTempos()`(`measureTempos` を carry-forward で作る。`[0]` はヘッダの開始テンポ)と
    `spbAt(i)` を追加し、`computeTimeline()` の先頭で `buildTempos()` を呼ぶ。
    `$('tempo')` を直読みしている6箇所を `spbAt(i)` に差し替える:
    `computeTimeline()`(682, 累積を `t += info.beats * spbAt(i)` の逐次積算に変える) /
    `buildClicks()`(744) / `beatPhase()`(824) / `loopRange()`(776) / `startSolo()`(1317) /
    `updateBear()`(1002, `curveK` を `measureTempos[phase.k]` から)。
  - 確認: テンポ anchor が1つだけの曲で今までと同じ挙動(回帰)。
    途中にテンポ変化を手で入れた meter で、小節バー・クリック音・指揮が同じ時刻でそろうこと。

- [x] 14. 保険の確認経路を `ts_source` ベースにする
  - 対象: `web/index.html`(`needStartConfirm` / `recascadeStart` / `initFromData`)
  - 内容: `needStartConfirm()` を、1小節目の `ts_source` が `carried`/`unknown`、
    または `tempo` anchor が無いときに `true` とする。
    `ts_source` が無い**旧フォーマットは従来の `confidence < 1` のまま**にする(後方互換。
    旧データは `tempo` も持たないので新条件をそのまま当てると既存の保存曲すべてで出てしまう)。
    `initFromData()` で1小節目に `tempo` anchor があればヘッダの開始テンポに反映する。
  - 確認: 保存済みの曲・サンプルを開いてポップアップが出ないこと(回帰)。
    `ts_source="unknown"` の meter を手で作ると出ること。

- [ ] 15. 調節画面で拍子をまとめて変更できるようにする
  - 対象: `web/index.html`(調節画面の拍子編集 / `recascadeStart`)
  - 内容: 既存の `recascadeStart()`(**先頭から**同じ拍子が続く範囲を一括変更)を、
    **任意の小節から**使えるように一般化する(`recascade(from, ts, beats)`)。
    小節の拍子を変えた直後、後ろに同じ(変更前の)拍子が続いていれば
    「後ろの N 小節も同じ拍子だっくま。まとめて変える？」と確認し、OK なら一括変更する。
    - **既定では一括しない**。聞いてから実行する(勝手に大量変更しない)
    - 一括変更した小節は `ts_source="user"` / `confidence=1` にする(紫破線を解除)
    - 別の拍子が現れたらそこで止める(既存 `recascadeStart` と同じ)
  - 理由: triage は1回しか読まないので、鉛筆の書き込み等で開始の拍子を誤読すると
    carry-forward で**全小節が同じ誤った拍子**になる(`tmp/ダンス1楽章2枚目.jpeg` で6回中2回発生)。
    1小節ずつ直すのは現実的でないため、まとめて直せることが「人が直す」方針の前提になる。
  - 確認: 全小節が `6/4` の状態で1小節目を `4/4` に直すと、後続もまとめて `4/4` になること。
    途中に別の拍子(`5/8` 等)があればそこで止まること。

### 通しの確認とドキュメント

- [ ] 16. 通しで確認して docs を更新する
  - 対象: `docs/pipeline.md` / `docs/spec.md` / `docs/todo.md`
  - 内容(確認): 2枚の写真で end-to-end を通す。
    - `tmp/ダンス２楽章.jpeg`(継続ページ): 聞かれて答えると **2/2・♩=268 で2拍振り**になること
    - 読める写真(曲頭): 何も聞かれず、従来と同じ結果になること(回帰)
  - 内容(docs):
    - `pipeline.md` — triage 段(`read_start.py`)を表に追加。`/process` が2段になったこと。
      meter の要に `ts_source` / `tempo` を追加。
      **「self-consistency=3回多数決」の記述を実態(`METER_RUNS` 既定1回 → confidence は常に 1.0)に直す**。
      「テンポ → フロントで設定」を「小節ごと(anchor。`read_start`/`read_meter` が読む)」に直す。
    - `spec.md` — 段階3のフィールド方針を更新(「テンポは曲頭の `tempo` のみ」→ 小節ごとの anchor、
      `ts_source` の追加)。段階3に triage を追記。
    - `todo.md` — 「拍子が画像から失われるケースへの耐性」を完了にし、
      decouple(AIに渡す画像だけ広くする)の第一歩が入ったことを書く。
      残りとして「②追加の画像処理の拡張(曲中の小節にも適用)」「曲中テンポ変化の人手補正」を積む。
