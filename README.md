# 指揮者っくま

楽譜の写真を読み込ませると、変拍子や GP のある曲でも「くま」が指揮してくれる練習 Web アプリ。
第5回 Agentic AI Hackathon with Google Cloud 提出作品。

- 公開URL: https://commander-kuma-web-service-513310957018.asia-northeast1.run.app
- 詳細設計: `docs/`（`pipeline.md` 処理の流れ / `conducting.md` 指揮ロジック / `hackathon.md` 応募要件 / `infrastructure.md` インフラ）

## 構成
- フロント: `web/index.html`（1ファイル。アップロード→調節→再生。しきくま/メトロノーム/譜読み）
- バックエンド: `spike/server.py`（FastAPI）。写真を段階処理して overlay/meter を返す
- 解析パイプライン: `detect_measures.py`(OpenCV) → `read_meter.py`(Vertex Gemini) → `read_beat_groups.py` → `export_overlay.py`
- 保存: 本番は GCS バケットを `/work` にマウントして永続化（jobs=ページ / pieces=曲）

## ローカル開発
Docker で起動（`/spike` `/web` をマウント、保存は `tmp/` に出る）:

```bash
docker build -t kuma-spike -f spike/Dockerfile .
docker run --rm -p 8080:8080 \
  -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
  -e GOOGLE_GENAI_USE_VERTEXAI=true \
  -e GOOGLE_CLOUD_PROJECT=gen-lang-client-0560903907 \
  -e GOOGLE_CLOUD_LOCATION=global \
  -e GEMINI_MODEL=gemini-3.5-flash \
  -v "$PWD/spike:/spike" -v "$PWD/web:/web" -v "$PWD/tmp:/work" \
  kuma-spike
# → http://localhost:8080
```

## 本番デプロイ（GCP / Cloud Run）

### 前提
- `gcloud` 認証: `gcloud auth login` と `gcloud auth application-default login`
- Docker Desktop 起動中
- GCP プロジェクト: `gen-lang-client-0560903907`（変える場合は環境変数で上書き可）

### 1. 初回セットアップ（一度だけ）
API 有効化 / Artifact Registry / GCS バケット / IAM 権限 をまとめて用意:

```bash
./scripts/setup-gcp.sh
```

このスクリプトがやること（再現のため明記）:
- API 有効化: `run` / `artifactregistry` / `aiplatform` / `storage`
- Artifact Registry(Docker) リポジトリ `commander-kuma-repository`（asia-northeast1）
- 保存用 GCS バケット `gs://commander-kuma-data`（均一アクセス）
- **Cloud Run ランタイムSA**（`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`）への権限:
  - `roles/storage.objectAdmin`（バケットに付与 … jobs/pieces の読み書き）
  - `roles/aiplatform.user`（プロジェクトに付与 … Gemini を Vertex AI で呼ぶ）

### 2. デプロイ（繰り返しOK）
```bash
./scripts/deploy.sh
```

ビルド(linux/amd64)→push→`gcloud run deploy` を実行。ポイント:
- **`--execution-environment=gen2`**（GCSマウントに必須）
- **`--add-volume` / `--add-volume-mount` で GCS バケットを `/work` にマウント**（保存の永続化。Cloud Run の FS は一時的なため）
- 環境変数で Vertex AI を使用（`GOOGLE_GENAI_USE_VERTEXAI=true` など）、モデルは `gemini-3.5-flash`
- `--allow-unauthenticated`（サンプル閲覧を認証なしに）、`--memory=2Gi --timeout=300`

### ハマりどころ（実際に踏んだ）
- **Dockerfile の起動コマンドは `uvicorn server:app`**（`main:app` ではない。`spike/main.py` は存在しない）
- **`COPY ./spike /spike` / `COPY ./web /web`**（絶対パス。`server.py` は `/spike` `/web` を直接参照。`/work` は GCS マウント先なので COPY しない）

## 注意 / 制約
- `yt-dlp`(YouTube音声抽出) は GCP 上だとボット判定でブロックされがち → ローカル開発専用。本番はメトロノーム軸＋音声アップロード方針
- Cloud Run の 12/1 まで稼働維持（ハッカソン規定）
