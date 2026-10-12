"""実験: AIに「各枠の拍を数えさせて」小節線の見落としを検出できるか試す。

幅・密集度は間接的な当て推量(たまたまの危険)。拍の合計が拍子を超えていれば小節線の
見落とし = これは音楽のルール上の直接の根拠。AIが拍を正しく数えられるかが鍵。

使い方: python experiment_beats.py <out_dir> [枠番号...]  (既定の対象は下の DEFAULT)
  枠55=真の見落とし(6拍), 枠44/45=段階1正解なのにAIが誤分割, 2/30/56=正常
結果は experiment_beats.json にキャッシュ。
"""
import json
import os
import sys
import time
from pathlib import Path

import cv2
import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT = [2, 30, 44, 45, 55, 56]
PAD = 12  # 切り出しの余白(px)

PROMPT = """各画像は、吹奏楽のパート譜から切り出した「小節候補」です。枠番号を付けています。
小節候補は、画像処理が小節線で区切った1区間です。小節線を見落とすと、1つの枠に複数小節が入ります。

各枠について、音楽のルールで検算してください:
- 拍子記号(読めれば)。枠の中に拍子が変わる箇所があればそれも。
- 見える音符と休符の長さを合計すると、何拍分あるか(例: 4分音符=1拍, 8分音符=0.5拍, 付点4分=1.5拍)。
- その拍数が「ちょうど1小節分」に収まっているか。収まらず複数小節分あるなら fits_one_measure=false。

鉛筆の手書き(音名・丸囲みなど)は無視してください。自信がなければ note に書いてください。"""


class BoxBeats(BaseModel):
    index: int = Field(description="枠番号")
    time_signature: str = Field(description="拍子(読めなければ空文字)。途中で変わるなら両方")
    beats_found: str = Field(description="音符・休符の合計が何拍分か(例: '6拍(4+2)')")
    fits_one_measure: bool = Field(description="ちょうど1小節に収まっているか")
    note: str = Field(description="根拠・自信のなさなど")


class Result(BaseModel):
    boxes: list[BoxBeats]


def ask(client, crops):
    contents = [PROMPT]
    for idx, data in crops:
        contents.append(f"枠{idx}:")
        contents.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=Result)
    for attempt in range(5):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config).parsed
        except (errors.ServerError, httpx.HTTPError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def main():
    out_dir = Path(sys.argv[1])
    indices = [int(a) for a in sys.argv[2:]] or DEFAULT
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY が未設定です")

    by_index = {b["index"]: b for b in json.loads((out_dir / "measures.json").read_text())}
    crops = []
    for i in indices:
        b = by_index[i]
        img = cv2.imread(str(out_dir / f"clean_{b['staff']:02d}.jpg"))
        h, w = img.shape[:2]
        x, y, bw, bh = b["bbox"]["x"], b["bbox"]["y"], b["bbox"]["w"], b["bbox"]["h"]
        crop = img[max(0, y - PAD):min(h, y + bh + PAD), max(0, x - PAD):min(w, x + bw + PAD)]
        crops.append((i, cv2.imencode(".jpg", crop)[1].tobytes()))

    cache = out_dir / "experiment_beats.json"
    if cache.exists():
        result = Result.model_validate_json(cache.read_text())
    else:
        result = ask(genai.Client(), crops)
        cache.write_text(result.model_dump_json(indent=2))

    truth = {55: "見落とし(6拍)", 44: "正常1小節", 45: "正常1小節"}
    print(f"model={MODEL}\n")
    for r in sorted(result.boxes, key=lambda r: r.index):
        t = truth.get(r.index, "正常")
        ok = "FIT " if r.fits_one_measure else "SPLIT"
        print(f"枠{r.index:>2} [{ok}] 拍子={r.time_signature or '?':>8} 拍={r.beats_found:<12} "
              f"(正解:{t})  {r.note}")


if __name__ == "__main__":
    main()
