"""振り方決定(段階3の追加): 不規則/複合拍子の小節を「何拍に分けて振るか」(beat_groups)を決める。

read_meter.py が拍子を読んだ後に実行。対象は分子が5以上の拍子(5/8, 7/8, 6/8, 9/8, 5/4, 7/4 等)。
素のクリーン画像(clean_NN.jpg)を切り出し、楽譜の連桁/手書きの区切りを最優先に、
読めなければ指揮の慣習(複合=3つずつ, 5=2+3, 7=2+2+3)で補って beat_groups を決める。
結果を meter_ai.json の beat_groups に統合する。詳細は docs/conducting.md。

使い方: python read_beat_groups.py <out_dir>
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
PAD = 16  # 切り出しの余白(px)

PROMPT = """各画像は、吹奏楽のパート譜から切り出した1小節です(拍子は各枠のラベルに記載)。
その小節を指揮で「何拍に分けて振るか」= 拍のまとまり(beat_groups)を決めてください。
単位は分母の音符の数で、合計は必ず分子になります(例: 5/8→合計5, 6/8→合計6, 5/4→合計5)。

決め方(上から優先):
1. 楽譜の8分音符の連桁(ビーム=音符をつなぐ横棒)や、手書きの区切り線・アクセントが分け方を
   示していれば、それに従う(composerの意図が最優先)。
2. 見えない/読めない場合は、指揮の慣習で補う:
   - 複合拍子(分子が3の倍数 6,9,12)→ 3つずつ。6/8→[3,3], 9/8→[3,3,3], 12/8→[3,3,3,3]
   - 不規則拍子(5,7 など)→ 2と3の組。5→[2,3], 7→[2,2,3](楽譜が 3+2 等を示せばそれを優先)
   - それ以外は 2・3 のまとまりに無理なく分ける

鉛筆の手書きは補助に使う。各枠に1つずつ、合計が分子になるよう答えてください。"""


class BeatGroups(BaseModel):
    index: int = Field(description="枠番号")
    beat_groups: list[int] = Field(description="拍のまとまり(分母音符の数)。例 5/8→[2,3], 6/8→[3,3]。合計=分子")


class Result(BaseModel):
    groups: list[BeatGroups]


def numerator(ts):
    return int(ts.split("+")[0].split("/")[0])


# 指揮の慣習による分け方(AIの答えが合計≠分子で不正な時のフォールバック)
def default_groups(num):
    conv = {5: [2, 3], 6: [3, 3], 7: [2, 2, 3], 8: [3, 3, 2],
            9: [3, 3, 3], 10: [3, 3, 2, 2], 11: [3, 3, 3, 2], 12: [3, 3, 3, 3]}
    if num in conv:
        return conv[num]
    g, r = [], num           # それ以外は3優先で2・3に分ける
    while r > 4:
        g.append(3); r -= 3
    if r:
        g.append(r)
    return g


def ask(client, crops):
    contents = [PROMPT]
    for idx, ts, data in crops:
        contents.append(f"枠{idx} ({ts}):")
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
    vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    if not vertex and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY が未設定です(または Vertex なら GOOGLE_GENAI_USE_VERTEXAI=true)")

    boxes = {m["index"]: m for m in json.loads((out_dir / "measures.json").read_text())}
    meter_path = out_dir / "meter_ai.json"
    meter = json.loads(meter_path.read_text())

    # 分子が5以上の拍子だけ対象(単純拍子 2/3/4 は分子ぶんで振るので不要。連結は除く)
    targets = [m for m in meter["measures"] if "+" not in m["ts"] and numerator(m["ts"]) >= 5]
    if not targets:
        print("分け方が要る拍子(分子5以上)は無し。何もしない")
        return

    crops = []
    for m in targets:
        b = boxes[m["index"]]["bbox"]
        staff = boxes[m["index"]]["staff"]
        img = cv2.imread(str(out_dir / f"clean_{staff:02d}.jpg"))
        h, w = img.shape[:2]
        x0, y0 = max(0, b["x"] - PAD), max(0, b["y"] - PAD)
        x1, y1 = min(w, b["x"] + b["w"] + PAD), min(h, b["y"] + b["h"] + PAD)
        crops.append((m["index"], m["ts"], cv2.imencode(".jpg", img[y0:y1, x0:x1])[1].tobytes()))

    result = ask(genai.Client(), crops)
    bg = {g.index: g.beat_groups for g in result.groups}

    # 検算: 合計が分子に合わない/空なら指揮慣習で補う
    final, fixed = {}, []
    for m in targets:
        num = numerator(m["ts"])
        g = bg.get(m["index"]) or []
        if sum(g) != num:
            g = default_groups(num)
            fixed.append(m["index"])
        final[m["index"]] = g

    for m in meter["measures"]:
        if m["index"] in final:
            m["beat_groups"] = final[m["index"]]
    meter_path.write_text(json.dumps(meter, ensure_ascii=False, indent=2))

    print(f"model={MODEL}  振り方決定 {len(targets)} 小節。慣習で補正: {fixed}")
    for m in targets:
        mark = "  <<< 慣習" if m["index"] in fixed else ""
        print(f"  枠{m['index']}: {m['ts']}  beat_groups={final[m['index']]}{mark}")


if __name__ == "__main__":
    main()
