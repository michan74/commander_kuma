#!/usr/bin/env bash
# 指揮者っくま: GCP 初回セットアップ(一度だけ実行)。
# Artifact Registry リポジトリ / GCS バケット / IAM 権限 を用意する。
# 前提: gcloud 認証済み(gcloud auth login & gcloud auth application-default login)。
set -euo pipefail

PROJECT_ID=${PROJECT_ID:-gen-lang-client-0560903907}
REGION=${REGION:-asia-northeast1}
REPO=${REPO:-commander-kuma-repository}
BUCKET=${BUCKET:-commander-kuma-data}

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"  # Cloud Run の既定ランタイムSA

echo "==> 必要なAPIを有効化"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com storage.googleapis.com

echo "==> Artifact Registry(Docker) リポジトリ: $REPO"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" \
       --repository-format=docker --location="$REGION" \
       --description="Docker repository for Cloud Run"

echo "==> 保存用GCSバケット: gs://$BUCKET (jobs/pieces を永続化)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" \
       --location="$REGION" --uniform-bucket-level-access

echo "==> ランタイムSA($RUNTIME_SA)に権限付与"
# GCS(保存の読み書き)
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role="roles/storage.objectAdmin"
# Vertex AI(Gemini で拍子・分け方を読む)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role="roles/aiplatform.user"

echo "==> 完了。次は ./scripts/deploy.sh でデプロイ"
