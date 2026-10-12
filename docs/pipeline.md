# 処理の流れ(パイプライン)

写真1枚 → 解析 → 練習まで。詳細な指揮ロジックは `docs/conducting.md`。

## 全体図

```
[写真] --アップロード--> サーバ(spike/server.py) ─ 段階ごとにスクリプト実行 ─> {overlay, meter} --> フロント(web/index.html) --> 練習
```

## サーバ側(POST /process で順に実行)

| 順 | スクリプト | 役割 | 主な出力 |
|---|---|---|---|
| 1 | `detect_measures.py`(段階1, OpenCV) | 五線検出→段ごと水平化→小節線検出。小節の「枠」を作る | `measures.json`(枠), `clean_NN.jpg`(素の段画像), `staff_NN.jpg`(描き込み), `staves.json`(変換行列) |
| 2 | `read_meter.py`(段階3, Gemini/Vertex) | 各枠の**拍子**を読む(self-consistency=3回多数決+信頼度)。予告拍子を次段に引き継ぐ | `meter_ai.json`(index, ts, beats, confidence) |
| 3 | `read_beat_groups.py`(**振り方決定**, Gemini) | 不規則/複合拍子の**分け方(beat_groups)**を決める(楽譜の連桁優先＋指揮慣習で補完) | meter_ai.json に `beat_groups` を追記 |
| 4 | `export_overlay.py` | 枠を元写真のピクセル座標のポリゴンに | `overlay.json` |

→ `/process` が `{job, image, overlay, meter}` を返す。補正は `/save/{job}`、曲は `/piece`(名前付き保存)。
ジョブは最新 `MAX_JOBS` 件だけ残す(曲に属すページは保護)。

※ 別系統: `youtube_beats.py`(yt-dlp→Librosa)→ `beats.json`(実拍モード用。ローカル専用、GCPでは不可)。

## データの要(meter)

`meter_ai.json` / `web/meter.json` の各小節:
```
{ index, ts: "5/8", beats: 2.5, beat_groups: [2,3], confidence: 1.0 }
```
- `ts` = 拍子(read_meter)
- `beat_groups` = 振り方=分け方(read_beat_groups)。8分数などの単位
- `beats` = 4分音符換算の長さ(タイムライン用)
- `confidence` = 拍子読みの信頼度(低いと紫破線で「要確認」)

## フロント側(web/index.html)

| 関数 | 役割 |
|---|---|
| `measurePulses(info)` | 拍子+beat_groups → **各小節の拍の配列**(felt beat。各値=4分換算の長さ) |
| `beatPhase(t)` | 再生時刻 t → 今の小節k・何拍目j・拍内の進み frac |
| `updateMetro / updateBear` | メトロノーム図形 / 指揮図形(`conductorFig`)を同じ拍で描画 |
| スケジューラ(setInterval) | 拍を Web Audio で先読み予約して発音(ジッタ減) |

### 画面の流れ(アップロード → 調節 → 再生)
- **調節(レビュー)**: 解析直後に入る。各小節の上に拍子を大きく表示。ツール=✏拍子編集 / ✂分割 / ⇔結合。「▶この内容で練習する」で再生へ。
- **再生**: 下記3モードで練習/同期。「← 調節に戻る」で調節へ。

### 3モード(表示切替・再生画面)
- **譜読み**: 楽譜に小節バー(ハイライト)
- **メトロノーム**: N角形+星(8分を小さい星)
- **しきくま**: 指揮図形を星がなぞる。始点固定・連続・**テンポで直線⇄曲線**(`docs/conducting.md`)

### 人間補正(調節画面に集約)
- **拍子・分け方**: 小節をクリック → 編集 → 即反映(人が直した小節は confidence=1 で紫破線解除)。
- **枠(小節境界)**: ✂分割=クリック位置で上下辺を内分して2枠に / ⇔結合=隣接2小節(同段)を順にクリックして1枠に。index を再採番し overlay/meter を同期(フロントの多角形幾何で完結)。
- **保存**: **💾名前をつけて保存** → `/save/{job}`(枠 overlay + 拍子 meter の両方を永続化)+ `/piece`。サンプルは localStorage。
- 低信頼(紫破線)の小節が「要確認」の導線

## どこで何が決まるか(迷ったとき)
- **拍子** → `read_meter.py`(楽譜を読む)
- **振り方=分け方(何拍で振るか)** → `read_beat_groups.py`(楽譜＋指揮慣習)＋フロント `measurePulses`
- **指揮図形の形** → フロント `conductorFig`(データ。手描きに差し替え予定)
- **動きの表現(カーブ等)** → フロント(テンポ連動)。将来はAI(`conducting.md` §6)
- **テンポ** → フロントで設定(playback)
