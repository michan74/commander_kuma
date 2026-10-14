#!/usr/bin/env bash
# 指揮者っくま: Cloud Run へデプロイ(ビルド→push→deploy)。繰り返し実行OK。
# 前提: ./scripts/setup-gcp.sh を一度実行済み。Docker Desktop 起動中。
set -euo pipefail

PROJECT_ID=${PROJECT_ID:-gen-lang-client-0560903907}
REGION=${REGION:-asia-northeast1}
REPO=${REPO:-commander-kuma-repository}
IMAGE=${IMAGE:-commander-kuma-web-app}
SERVICE=${SERVICE:-commander-kuma-web-service}
BUCKET=${BUCKET:-commander-kuma-data}
GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash}
IMG="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE:latest"

cd "$(dirname "$0")/.."  # リポジトリルートへ

echo "==> Docker 認証"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

echo "==> ビルド ($IMG)"
# NOTE: Cloud Run は linux/amd64。Apple Silicon でも --platform で合わせる
docker build -t "$IMG" -f spike/Dockerfile --platform linux/amd64 .

echo "==> push"
docker push "$IMG"

echo "==> Cloud Run デプロイ(/work に GCS をマウント)"
# NOTE: GCSマウントは第2世代実行環境(gen2)が必須
gcloud run deploy "$SERVICE" \
  --image="$IMG" \
  --region="$REGION" \
  --execution-environment=gen2 \
  --add-volume="name=kuma-data,type=cloud-storage,bucket=$BUCKET" \
  --add-volume-mount="volume=kuma-data,mount-path=/work" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=$GEMINI_MODEL" \
  --allow-unauthenticated --memory=2Gi --timeout=300

echo "==> URL:"
gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'
