"""段階3の前段(triage): 開始の拍子・テンポが読めるかを判定する。

拍子もテンポも曲の1番最初の小節に必ず記載があるので、そこだけを見る。
読めなければ拡大して1回やり直し、それでも読めなければ人に聞く項目を出す。

本読み(read_meter.py)の前に置くのは、拍子が carry-forward で後続に伝播するため。
1小節目を誤ると、そこから先の小節がすべてその誤りを引き継いで出力が丸ごと汚染される。

使い方: python read_start.py <out_dir> <元写真>
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

from tempo import quarter_bpm, word_bpm

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
JPEG_QUALITY = 92

PROMPT = """吹奏楽のパート譜を撮った写真から、ページの先頭(1段目とその上)を切り出した画像です。

この画像から「曲の1番最初の小節の拍子」と「テンポ」が読み取れるかを判定してください。
拍子記号とテンポ表示は、曲の1番最初の小節に必ず印刷される決まりです。
拍子記号は五線の左端(音部記号・調号の直後)、テンポ表示は五線のすぐ上にあります。

重要: 読めたフリをしないでください。
- 見つからないときに 4/4 や ♩=120 と推測しては**いけません**。
- 画像に印刷が無いなら state="absent" と答えてください。曲の途中のページを撮った写真では、
  拍子記号もテンポ表示も前のページにあるため、この画像には存在しません。
- 印刷はありそうだが判読できない(紙の端で切れている・小さすぎる・ぼけている)場合は
  state="unreadable" と答えてください。

テンポの表記は2通りあります。
- 数値: 「♩=134」「𝅗𝅥=72」「♩.=60」 → state="printed"。value に数字、unit に音符の種類。
- 速度標語: 「Andante」「Allegro」 → state="text_only"。word に標語を**文字のまま**入れる。
  **標語をBPMの数値に変換しないでください。** 変換はこちらで行います。
- 「Andante (♩=72)」のように両方あれば数値を優先して state="printed"、word にも標語を入れる。

1段目の左に小節番号(五線の左上の小さい数字)が印刷されていれば first_measure_number に入れてください。
無ければ 0 にしてください。曲名・作曲者・パート名は判定に使いません。

鉛筆の手書き(音名・丸囲み・指番号など)は無視してください。"""


# NOTE: 「読めなかった」は空文字(first_measure_number は 0)で表す。read_meter.py が
#   beat_groups の空配列を同じ意味で使っているのに合わせた。
class ItemRead(BaseModel):
    state: str = Field(description="printed=読めた / text_only=速度標語だけ(テンポのみ) / "
                                   "unreadable=ありそうだが判読できない / absent=印刷が無い")
    value: str = Field(description="読めた値。拍子は 4/4 の形、テンポは数字だけ。読めなければ空文字")
    unit: str = Field(description="テンポのみ。音符の種類 quarter / half / eighth / "
                                  "dotted_quarter / dotted_half / whole。無ければ空文字")
    word: str = Field(description="テンポのみ。速度標語を画像の表記のまま(例 Andante con moto)。"
                                  "数値に変換しないこと。無ければ空文字")
    text: str = Field(description="画像に書かれていた通りの表記(例 I Ritmico (♩=134))。無ければ空文字")
    note: str = Field(description="読めなかった理由(端で切れている / 小さすぎる / 印刷が無い など)")


class StartTriage(BaseModel):
    first_measure_number: int = Field(description="1段目の左に印刷された小節番号。無ければ 0")
    time_signature: ItemRead
    tempo: ItemRead


def crop_head(img, staff, mode):
    """ページ頭を切り出す。head=1段目とその1段分上まで / zoom=その左半分"""
    c = staff["crop"]
    # NOTE: 1段目より上は、定義上まるごとページのヘッダ(曲名・作曲者・テンポ・小節番号)。
    #   「上に space*N」のような定数では切らない。テンポ表示の位置は楽譜ごとに違い、
    #   写真の余白の取り方でも変わるので、1枚に合わせた定数は次の写真で外れる。
    #   代わりに1段分(c.h)を歯止めにする。机や余白が大きく写った写真で青天井にしないため
    #   (手元の2枚はどちらも1段目が写真上端に近く、この歯止めは効いていない)。
    crop = img[max(0, c["y"] - c["h"]):c["y"] + c["h"], :]
    if mode == "zoom":
        # NOTE: 効くのは「視野を狭めること」。モデルは大きい画像を一定の予算まで縮小するので、
        #   範囲を狭めると同じ予算が狙いに使われて小さい文字が残る。拡大(resize)はしない
        #   — 無いディテールは作れないので情報が増えず、バイト数が増えるだけ。
        # NOTE: 横半分なのは、拍子記号(段の先頭)もテンポ表示(五線の上・左寄り)も必ずそこに
        #   収まるため。dance_movement 実測でテンポ表記の右端 x=745 に対し半分=1425。
        #   head より狭いが狙いは切り落とさない、という関係を保つのが要点。
        crop = crop[:, 0:min(img.shape[1], c["x"] + c["w"] // 2)]
    return crop


def encode(img):
    """AI に渡す JPEG のバイト列にする"""
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])[1].tobytes()


def ask(client, data):
    """ページ頭の画像1枚を渡して、開始の拍子・テンポが読めるかを判定させる"""
    contents = [PROMPT, types.Part.from_bytes(data=data, mime_type="image/jpeg")]
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=StartTriage)
    for attempt in range(5):
        try:
            return client.models.generate_content(
                model=MODEL, contents=contents, config=config).parsed
        except (errors.ServerError, httpx.HTTPError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def resolve_ts(item):
    """拍子が確定したか。確定していれば "4/4" のような値、していなければ None"""
    return item.value if item.state == "printed" and item.value else None


def resolve_tempo(item):
    """テンポを4分換算BPMにする。確定していなければ None"""
    if item.state == "printed":
        try:
            return quarter_bpm(float(item.value), item.unit)
        except (TypeError, ValueError):
            return None
    if item.state == "text_only":
        # NOTE: 標語→BPM は AI にさせずここで表を引く(tempo.TEMPO_WORDS)。
        #   表に無い標語・どの標語か決まらない表記は None になり、人に聞く方へ回る。
        return word_bpm(item.word or item.text)
    return None


def decide(triage, zoomed):
    """次の手を選ぶ。(proceed|zoom|ask, 読めなかった項目) を返す"""
    items = {"ts": triage.time_signature, "tempo": triage.tempo}
    resolved = {"ts": resolve_ts(items["ts"]), "tempo": resolve_tempo(items["tempo"])}
    missing = [k for k, v in resolved.items() if v is None]
    if not missing:
        return "proceed", []
    # NOTE: 狭く切り直して効くのは「あるのに読めない」ときだけ。"absent"(そこに無い)は
    #   何度切り直しても出てこないので飛ばして人に聞く。ズームは1回だけ(zoomed で止める)。
    if not zoomed and any(items[k].state == "unreadable" for k in missing):
        return "zoom", missing
    return "ask", missing


def show(label, item):
    extra = "".join(f" {k}={v!r}" for k, v in
                    (("value", item.value), ("unit", item.unit), ("word", item.word)) if v)
    print(f"  {label}: {item.state}{extra}")
    if item.text:
        print(f"      表記: {item.text}")
    if item.note:
        print(f"      note: {item.note}")


def main():
    out_dir, src = Path(sys.argv[1]), Path(sys.argv[2])
    vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    if not vertex and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY が未設定です(または Vertex なら GOOGLE_GENAI_USE_VERTEXAI=true)")

    staves = json.loads((out_dir / "staves.json").read_text())
    if not staves:
        sys.exit("段が見つかっていない(段階1が失敗している)")

    img = cv2.imread(str(src))
    client = genai.Client()
    attempts = []
    for mode in ("head", "zoom"):
        crop = crop_head(img, staves[0], mode)
        data = encode(crop)
        (out_dir / f"start_{mode}.jpg").write_bytes(data)
        triage = ask(client, data)
        action, missing = decide(triage, zoomed=(mode == "zoom"))
        attempts.append({"pass": mode, "ts": triage.time_signature.state,
                         "tempo": triage.tempo.state, "next": action})
        print(f"[{mode}] {crop.shape[1]}x{crop.shape[0]} {len(data) / 1024:.0f}KB "
              f"小節番号={triage.first_measure_number or '(無し)'} → {action}")
        show("拍子", triage.time_signature)
        show("テンポ", triage.tempo)
        if action != "zoom":
            break

    ts, tempo = triage.time_signature, triage.tempo
    start = {
        "status": "ok" if action == "proceed" else "ask",
        # NOTE: 判定には使わない。🐻 の文面を「60小節目の〜」と正確にするためだけに読む
        "first_measure_number": triage.first_measure_number or None,
        "ts": {"value": resolve_ts(ts), "state": ts.state, "text": ts.text or None},
        "tempo": {"value": resolve_tempo(tempo), "state": tempo.state, "unit": tempo.unit or None,
                  "word": tempo.word or None, "text": tempo.text or None},
        "ask": missing,
        "attempts": attempts,
    }
    (out_dir / "start.json").write_text(json.dumps(start, ensure_ascii=False, indent=2))
    print(f"model={MODEL}  status={start['status']}"
          + (f"  聞く項目={missing}" if missing else "  (何も聞かない)"))


if __name__ == "__main__":
    main()
