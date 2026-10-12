"""段階2の精度を上げるための実験台。

今の段階2は「描き込み画像 + 私の間違いを直して」方式。精度が低い(密集段で誤分割・誤結合)。
ここでは入力の渡し方を変えて、どれが一番正確に小節を読めるかを試す。

使い方:
  python experiment.py <out_dir> [style]
    style = clean   (既定) 描き込みなしの素の段画像(clean_NN.jpg)
            boxes          現状の描き込み画像(staff_NN.jpg)

AIには「各段に小節がいくつ・どこか」を素直に読ませる(間違い探しではなく直接読み)。
結果は experiment_<style>.json にキャッシュし、再実行では消費しない。
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")

PROMPT = """吹奏楽のパート譜の各段(五線1行)を、水平になるよう切り出した画像を段の順に並べます。

各段について、小節がいくつあるかを数えてください。小節は小節線(五線を縦に貫く線)で区切られます。

注意:
- 音符の符尾(たて棒)や和音、鉛筆の書き込み(音名・丸囲みなど)は小節線ではありません。
- 段の先頭の音部記号・拍子記号だけの部分は小節に数えません。
- 段の最後に次の段の予告として小さく書かれた拍子記号は小節に数えません。
- 拍子が変わる小節があれば、その拍子も教えてください(5/8, 7/8 などの変拍子に注意)。

各段(staff 番号)について、小節の数と、各小節が「段の左から何番目の区切りか・何拍子か・目印(練習番号や強弱など)」を簡潔に説明してください。"""


class Measure(BaseModel):
    time_signature: str = Field(description="その小節の拍子(読み取れなければ空文字)")
    note: str = Field(description="場所の目印(段の左からの位置、練習番号、強弱など)")


class StaffRead(BaseModel):
    staff: int
    measure_count: int = Field(description="この段の小節数")
    measures: list[Measure]


class Result(BaseModel):
    staves: list[StaffRead]


def ask(client, images):
    contents = [PROMPT]
    for si, path in images:
        contents.append(f"段{si}:")
        contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg"))
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
    style = sys.argv[2] if len(sys.argv) > 2 else "clean"
    prefix = {"clean": "clean", "boxes": "staff"}[style]
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY (または GOOGLE_API_KEY) が未設定です")

    measures = json.loads((out_dir / "measures.json").read_text())
    staves = sorted({m["staff"] for m in measures})
    current = {si: sum(1 for m in measures if m["staff"] == si) for si in staves}
    images = [(si, out_dir / f"{prefix}_{si:02d}.jpg") for si in staves]

    cache = out_dir / f"experiment_{style}.json"
    if cache.exists():
        result = Result.model_validate_json(cache.read_text())
    else:
        result = ask(genai.Client(), images)
        cache.write_text(result.model_dump_json(indent=2))

    read = {s.staff: s for s in result.staves}
    print(f"style={style}  段ごとの小節数: 段階1(現状) vs AI素直読み\n")
    diff_total = 0
    for si in staves:
        c = current[si]
        a = read[si].measure_count if si in read else None
        mark = "" if a == c else "  <<< ちがう"
        diff_total += abs((a or 0) - c)
        sig = " ".join(m.time_signature for m in read[si].measures if m.time_signature) if si in read else ""
        print(f"  段{si}: 段階1={c:>2}  AI={a if a is not None else '-':>2}{mark}   拍子: {sig}")
    print(f"\n合計: 段階1={sum(current.values())}  AI={sum(s.measure_count for s in result.staves)}  差の総和={diff_total}")


if __name__ == "__main__":
    main()
