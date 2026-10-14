FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# NOTE: librosa は音声の拍・テンポ抽出(段階2: YouTube処理)、yt-dlp は音声抽出に使う。
#   oemer は段階1で却下し未使用なので外した(ハッシュ固定依存が librosa と衝突するため)。
#   numpy<1.24 は opencv の都合。librosa 0.10 / numba 0.58 はこの numpy で動く
RUN pip install --no-cache-dir "numpy<1.24" "opencv-python<4.10" google-genai \
    "librosa<0.11" yt-dlp

# NOTE: Web バックエンド(spike/server.py)用。別レイヤーにして上の重い依存のキャッシュを守る
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart

# server.py は /spike(スクリプト) と /web(フロント) を絶対パスで参照。/work は Cloud Run で GCS をマウント
COPY ./spike /spike
COPY ./web /web

WORKDIR /spike
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]