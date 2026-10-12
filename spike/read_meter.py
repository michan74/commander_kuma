"""段階3(の一部): 各小節の拍子を Gemini に読ませる。

段階2までで小節の枠(番号)は確定している。ここでは枠を「数えさせず」、
確定した各枠番号について拍子記号だけを答えさせる(数え間違いを防ぐ)。
出力 read_meter.json を、手動で読み取った meter.json(web/)と答え合わせできる。

使い方: python read_meter.py <out_dir> [GEMINI_MODEL は環境変数で上書き可]
"""
import json
import os
import sys
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

PROMPT = """吹奏楽のパート譜の各段(五線1行)を、水平に切り出した画像を段の順に並べます。
各画像にはオレンジの番号付きの長方形で「小節(枠)」を描いてあります。この番号が枠番号です。

拍子記号(4/4, 5/8, 2/4 など)は、拍子が変わる小節の先頭にだけ印刷されます。
枠に拍子記号の印刷が無ければ、その小節は前の小節と同じ拍子です。
重要: 拍子記号は段の切れ目では繰り返されません(音部記号・調号は毎段繰り返すが拍子は違う)。
段の最初の枠に拍子記号が無い場合、前の段の最後に小さく書かれた「予告の拍子記号」があれば
それがその段の最初の小節の拍子です。無ければ前の段の最後の小節と同じ拍子です。
渡した段は順番に並んでいるので、前の段の情報を使って判断してください。

渡したすべての枠番号について、その小節の拍子を答えてください。
- 1つの枠の途中で拍子が変わる(小節線の見落としで2小節が1枠に入っている)場合は、
  time_signature に "4/4+2/4" のように連結し、changes_midbox=true にしてください。
- 分母が8の不規則拍子(5/8, 7/8 など)は、8分音符の連桁(ビーム=音符をつなぐ横棒)の区切りが
  拍のまとまりを表します。その区切りを8分音符の数で beat_groups に入れてください
  (例: 5/8 で「2つ+3つ」に連桁されていれば [2,3])。4分系(x/4)や判断できない時は空配列。
- 鉛筆の手書き(音名・丸囲みなど)は無視してください。
枠を増やしたり減らしたりせず、渡した枠番号ちょうどに1つずつ答えてください。

さらに、各段の一番右(最後の小節線より後ろ)に、次の段への「予告の拍子記号」が小さく
書かれていることがあります。見つけたら end_courtesies に {staff, ts} で報告してください
(これは次の段の最初の小節の拍子になります)。無ければ空配列で構いません。"""


class MeasureMeter(BaseModel):
    index: int = Field(description="枠番号")
    time_signature: str = Field(description="拍子(例 4/4, 5/8)。途中で変わるなら 4/4+2/4 のように連結")
    changes_midbox: bool = Field(description="1つの枠の途中で拍子が変わるか")
    beat_groups: list[int] = Field(
        description="分母が8の不規則拍子(5/8,7/8等)で、音符の連桁(ビーム)の区切りが示す拍の"
                    "まとまりを8分音符の数で(例 5/8の2+3なら[2,3])。4分系(x/4)や読めない時は空配列")


class StaffCourtesy(BaseModel):
    staff: int = Field(description="段の番号")
    ts: str = Field(description="その段の最後に小さく書かれた、次段への予告拍子")


class Result(BaseModel):
    measures: list[MeasureMeter]
    end_courtesies: list[StaffCourtesy] = Field(
        description="各段の末尾に予告拍子(次段最初の小節の拍子)が書かれていれば報告。無ければ空")


def ask(client, out_dir, indices_by_staff):
    contents = [PROMPT]
    for si, indices in indices_by_staff.items():
        contents.append(f"段{si} (枠番号 {indices}): staff_{si:02d}.jpg")
        contents.append(types.Part.from_bytes(
            data=(out_dir / f"staff_{si:02d}.jpg").read_bytes(), mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=Result)
    for attempt in range(5):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config).parsed
        except (errors.ServerError, httpx.HTTPError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def beats_of(ts):
    # NOTE: 4分音符を1拍とした拍数。"4/4"→4, "5/8"→2.5。連結 "4/4+2/4"→6 は各項の和
    total = Fraction(0)
    for part in ts.split("+"):
        n, d = part.strip().split("/")
        total += Fraction(int(n) * 4, int(d))
    return float(total)


def apply_courtesy(result, first_of_staff):
    # NOTE: 予告拍子を次段の最初の小節に引き継ぐ。拍子は段をまたいで繰り返されないため、
    #   段末の予告拍子が次段先頭の小節の拍子になる(例: staff7末尾の2/4 → staff8先頭 枠51)
    by_index = {m.index: m for m in result.measures}
    for c in result.end_courtesies:
        nxt = first_of_staff.get(c.staff + 1)
        if nxt is not None and nxt in by_index:
            by_index[nxt].time_signature = c.ts


def main():
    out_dir = Path(sys.argv[1])
    # NOTE: self-consistency。METER_RUNS 回読ませて小節ごとに多数決し、割れ具合を信頼度にする。
    #   既定1回(サーバの通常処理は速さ優先)。信頼度が欲しい時は METER_RUNS=3 等で呼ぶ
    runs = max(1, int(os.environ.get("METER_RUNS", "1")))
    vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    if not vertex and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY が未設定です(または Vertex なら GOOGLE_GENAI_USE_VERTEXAI=true)")

    measures = json.loads((out_dir / "measures.json").read_text())
    staves = sorted({m["staff"] for m in measures})
    indices_by_staff = {si: [m["index"] for m in measures if m["staff"] == si] for si in staves}
    first_of_staff = {si: min(idxs) for si, idxs in indices_by_staff.items()}
    all_idx = sorted(m["index"] for m in measures)

    cache = out_dir / "read_meter.json"
    client = genai.Client()
    results = []
    if runs == 1 and cache.exists():
        r = Result.model_validate_json(cache.read_text())
        apply_courtesy(r, first_of_staff)
        results = [r]
    else:
        for _ in range(runs):
            r = ask(client, out_dir, indices_by_staff)
            apply_courtesy(r, first_of_staff)
            results.append(r)
        cache.write_text(results[-1].model_dump_json(indent=2))

    ts_by_run = [{m.index: m.time_signature for m in r.measures} for r in results]
    bg_last = {m.index: m.beat_groups for m in results[-1].measures}

    out = []
    for i in all_idx:
        votes = Counter(run[i] for run in ts_by_run if i in run)
        if not votes:
            continue
        ts, cnt = votes.most_common(1)[0]
        try:
            b = beats_of(ts)
        except Exception:
            b = None
        out.append({"index": i, "ts": ts, "beats": b, "beat_groups": bg_last.get(i, []),
                    "confidence": round(cnt / len(results), 2)})
    (out_dir / "meter_ai.json").write_text(
        json.dumps({"measures": out}, ensure_ascii=False, indent=2))

    low = [m for m in out if m["confidence"] < 1.0]
    print(f"model={MODEL} runs={runs}  {len(out)}枠  低信頼(読みが割れた)={len(low)}")
    for m in low:
        print(f"  枠{m['index']}: {m['ts']} conf={m['confidence']}")


if __name__ == "__main__":
    main()
