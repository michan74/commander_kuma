"""最小バックエンド: 写真をアップロード → 段階1(小節検出)→ 段階3(Gemini拍子読み)→ overlay を返す。
フロント(web/)も配信する。コンテナ内で uvicorn で起動する前提。

起動例:
  docker run --rm -p 8000:8000 \
    -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
    -e GOOGLE_GENAI_USE_VERTEXAI=true -e GOOGLE_CLOUD_PROJECT=... -e GOOGLE_CLOUD_LOCATION=global \
    -e GEMINI_MODEL=gemini-3.5-flash \
    -v "$PWD/spike:/spike" -v "$PWD/web:/web" -v "$PWD/tmp:/work" -w /spike \
    kuma-spike uvicorn server:app --host 0.0.0.0 --port 8000
"""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


def job_data(job):
    d = JOBS / job
    if not d.exists():
        return None
    # 補正済み(meter_corrected / overlay_corrected)があればそれを、無ければ元の読みを返す
    meter = d / "meter_corrected.json"
    if not meter.exists():
        meter = d / "meter_ai.json"
    overlay = d / "overlay_corrected.json"
    if not overlay.exists():
        overlay = d / "overlay.json"
    start = d / "start.json"
    # NOTE: /process/detect の時点では meter がまだ無い(拍子は後段で読む)。meter=None を返して
    #   フロントが「枠だけある状態」を描けるようにする
    return {
        "image": f"/jobs/{job}/input.jpg",
        "overlay": json.loads(overlay.read_text()) if overlay.exists() else None,
        "meter": json.loads(meter.read_text()) if meter.exists() else None,
        "start": json.loads(start.read_text()) if start.exists() else None,
    }

SPIKE = Path("/spike")
JOBS = Path("/work/jobs")         # ページ(写真1枚ぶんの解析結果)
PIECES = Path("/work/pieces")     # 曲(名前 + ページの並び)
JOBS.mkdir(parents=True, exist_ok=True)
PIECES.mkdir(parents=True, exist_ok=True)
# NOTE: 保存してない(曲に属さない)ジョブは最新 MAX_JOBS 件だけ残しLRU削除。曲のページは守る
MAX_JOBS = int(os.environ.get("MAX_JOBS", "20"))

app = FastAPI()


def touch(d):
    os.utime(d, None)  # LRU: 最近使った印に更新時刻を更新


def referenced_jobs():
    refs = set()
    for f in PIECES.glob("*.json"):
        try:
            refs.update(json.loads(f.read_text()).get("pages", []))
        except Exception:
            pass
    return refs


def prune_jobs():
    refs = referenced_jobs()  # 曲に属すページは消さない
    unref = sorted((d for d in JOBS.iterdir() if d.is_dir() and d.name not in refs),
                   key=lambda d: d.stat().st_mtime, reverse=True)
    for d in unref[MAX_JOBS:]:
        shutil.rmtree(d, ignore_errors=True)


def run(script, *args):
    subprocess.run(["python", script, *map(str, args)],
                   cwd=SPIKE, check=True, capture_output=True, text=True)


def apply_answer(d, body):
    """人が答えた開始情報を start.json に反映して確定させる。

    body: {"ts": "2/2", "tempo": 268} (4分換算BPM。どちらも任意)
    """
    path = d / "start.json"
    start = json.loads(path.read_text()) if path.exists() else {"ts": {}, "tempo": {}}
    if body.get("ts"):
        start["ts"] = {"value": body["ts"], "state": "user", "text": body.get("ts_text")}
    if body.get("tempo"):
        start["tempo"] = {"value": int(body["tempo"]), "state": "user",
                          "unit": body.get("unit"), "word": None, "text": body.get("tempo_text")}
    start["ask"] = [k for k in ("ts", "tempo") if not start.get(k, {}).get("value")]
    start["status"] = "ask" if start["ask"] else "ok"
    path.write_text(json.dumps(start, ensure_ascii=False, indent=2))


# 解析は2段に分かれる。前段(detect)で枠と「開始の拍子・テンポが読めたか」を返し、
# 読めなければフロントが人に聞いてから後段(read)を呼ぶ。
# NOTE: 人の答えを後段のプロンプトに渡したいので、前段と後段を並行には走らせない。
#   1小節目の拍子を誤ると carry-forward で後続すべてに伝播するため、読む前に確定させる。
@app.post("/process/detect")
async def process_detect(file: UploadFile = File(...)):
    job = uuid.uuid4().hex[:8]
    d = JOBS / job
    d.mkdir(parents=True, exist_ok=True)
    img = d / "input.jpg"
    img.write_bytes(await file.read())
    try:
        run("detect_measures.py", img, d)   # 段階1: 小節検出(約2秒)
        run("export_overlay.py", d, img)    # 小節を元写真座標のポリゴンに
        run("read_start.py", d, img)        # triage: 開始の拍子・テンポが読めるか
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": (e.stderr or str(e))[-2000:]})
    return {"job": job, **job_data(job)}


# 後段。body に人の回答({ts, tempo})があれば start.json に反映してから本読みに進む
@app.post("/process/read/{job}")
def process_read(job: str, body: dict = Body(default={})):
    d = JOBS / job
    if not d.exists():
        return JSONResponse(status_code=404, content={"error": "not found"})
    if body:
        apply_answer(d, body)
    try:
        run("read_meter.py", d)             # 段階3: Gemini(Vertex)で拍子・テンポ読み
        run("read_beat_groups.py", d)       # 不規則拍子(5/8等)のグループをクリーン切り出しで読む
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": (e.stderr or str(e))[-2000:]})
    prune_jobs()  # 最新 MAX_JOBS 件だけ残す
    return {"job": job, **job_data(job)}


# 保存したジョブ(補正込み)を再取得(リロード復元用)
@app.get("/job/{job}")
def get_job(job: str):
    data = job_data(job)
    if data is None:
        return JSONResponse(status_code=404, content={"error": "not found"})
    touch(JOBS / job)  # アクセスした=最近使った(LRUで残す)
    return data


# 人間の補正(拍子 meter + 枠 overlay)をジョブに保存。次回ロード時に反映される
# NOTE: 分割・結合で overlay(枠)も変わるので meter と両方受け取る。
#       旧クライアント互換で、meter 単体({measures:...})が来たら meter 補正として扱う。
@app.post("/save/{job}")
def save_job(job: str, body: dict = Body(...)):
    d = JOBS / job
    if not d.exists():
        return JSONResponse(status_code=404, content={"error": "not found"})
    meter = body.get("meter", body if "measures" in body else None)
    overlay = body.get("overlay")
    if meter is not None:
        (d / "meter_corrected.json").write_text(json.dumps(meter, ensure_ascii=False, indent=2))
    if overlay is not None:
        (d / "overlay_corrected.json").write_text(json.dumps(overlay, ensure_ascii=False, indent=2))
    touch(d)
    return {"ok": True}


# 保存した曲の一覧(再開のライブラリ用)
@app.get("/pieces")
def list_pieces():
    out = []
    for f in PIECES.glob("*.json"):
        try:
            p = json.loads(f.read_text())
            pages = p.get("pages", [])
            out.append({"id": f.stem, "name": p.get("name", "(無題)"),
                        "created": p.get("created", 0), "pageCount": len(pages),
                        "thumb": f"/jobs/{pages[0]}/input.jpg" if pages else None})
        except Exception:
            pass
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


# 曲を取得(各ページの画像・overlay・補正meter を並べて返す)
@app.get("/piece/{pid}")
def get_piece(pid: str):
    f = PIECES / f"{pid}.json"
    if not f.exists():
        return JSONResponse(status_code=404, content={"error": "not found"})
    p = json.loads(f.read_text())
    pages = [job_data(j) for j in p.get("pages", [])]
    pages = [pg for pg in pages if pg]  # 消えたページは除く
    for j in p.get("pages", []):
        if (JOBS / j).exists():
            touch(JOBS / j)  # LRUで守る
    return {"id": pid, "name": p.get("name", ""), "pages": pages}


# 曲を保存/更新(名前 + ページの並び)。id 省略で新規
@app.post("/piece")
def save_piece(body: dict = Body(...)):
    import time
    pid = body.get("id") or uuid.uuid4().hex[:8]
    f = PIECES / f"{pid}.json"
    created = json.loads(f.read_text()).get("created") if f.exists() else int(time.time())
    f.write_text(json.dumps({
        "name": body.get("name", "(無題)"),
        "created": created or int(time.time()),
        "pages": body.get("pages", []),
    }, ensure_ascii=False, indent=2))
    return {"id": pid}


app.mount("/jobs", StaticFiles(directory=str(JOBS)), name="jobs")
app.mount("/", StaticFiles(directory="/web", html=True), name="web")
