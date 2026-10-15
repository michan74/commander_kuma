# 審査用サンプル画像の置き場所

ハッカソン審査員が「審査用サンプル」から選ぶ楽譜写真をここに置く。

## 置き方
このフォルダに楽譜写真を保存し、`web/index.html` の `JUDGE_SAMPLES` 配列で
ファイル名と表示名を指定する。現在の設定:

- `candide.jpeg` … Candide
- `dance_movement1.jpeg` … Dance Movement I
- `dance_movement2.jpeg` … Dance Movement II

ファイル名・表示名・枚数を変えたい場合は `JUDGE_SAMPLES` を編集する。

## サムネイル
一覧表示は軽量のため `thumb/` のサムネイル(長辺400px)を使い、解析には元のフル解像度を渡す。
AIは小さな文字(テンポ・拍子)を crop で読むので、解析用の解像度は落とさない。
写真を差し替えたらサムネイルも作り直す:

```bash
cd web/judge
for f in candide dance_movement1 dance_movement2; do
  sips -Z 400 --setProperty formatOptions 70 "$f.jpeg" --out "thumb/$f.jpg"
done
```

## 動作
TOP画面「審査用サンプル」→ 写真を1枚選ぶと、通常アップロードと同じ解析フロー
（`/process/detect` → 開始の拍子・テンポ確認 → `/process/read` → 調整画面）が走る。
