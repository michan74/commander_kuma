# インフラ

> 保存(jobs/pieces/補正)は **GCS バケットを `/work` にマウント**して永続化する(Cloud Run のFSは一時的なため)。
> `server.py` は `/work` 配下を読み書きするだけなので **コード変更は不要**。ローカル開発は従来どおり `tmp/` をマウント。
> ユーザー管理は無し(**共有プール**: 全員同じ jobs/pieces を見る)。

## Step 0: GCS バケットを作成する(保存の永続化)

### 1. 変数(Step1と共通)
REGION="asia-northeast1"
PROJECT_ID=gen-lang-client-0560903907
BUCKET_NAME="commander-kuma-data" # 保存用バケット(jobs/pieces をそのまま置く)

### 2. バケット作成(東京, 均一アクセス)
gcloud storage buckets create gs://$BUCKET_NAME \
    --location=$REGION \
    --uniform-bucket-level-access

### 3. Cloud Run のランタイムSAに読み書き権限(objectAdmin)
# デフォルトのランタイムSA = <PROJECT_NUMBER>-compute@developer.gserviceaccount.com
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding gs://$BUCKET_NAME \
    --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

## Step 1: Artifact Registry にリポジトリを作成する

### 1. 共通で使う変数を設定（環境に合わせて変更してください）
REGION="asia-northeast1" # 東京リージョン
REPO_NAME="commander-kuma-repository" # リポジトリ名
IMAGE_NAME="commander-kuma-web-app" # イメージ名
PROJECT_ID=gen-lang-client-0560903907

### 2. Artifact RegistryにDockerリポジトリを作成
gcloud artifacts repositories create $REPO_NAME \
    --repository-format=docker \
    --location=$REGION \
    --description="Docker repository for Cloud Run"

### 3. DockerがArtifact Registryにアクセスできるように認証を通す
gcloud auth configure-docker $REGION-docker.pkg.dev

## Step2: Dockerイメージをビルドしてプッシュする

### 1. Artifact Registry用のタグを付けてビルド
docker build \
  -t $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest \
  -f spike/Dockerfile \
  --platform linux/amd64 .


### 2. イメージをプッシュ
docker push $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest


## Step 3: Cloud Run にデプロイする

# NOTE: GCSマウントには第2世代実行環境(gen2)が必須。--add-volume で /work にバケットをマウントし保存を永続化。
#       GEMINI_MODEL は実動作で手動正解と100%一致した gemini-3.5-flash を使う(2.5はローカル検証で劣る)。
gcloud run deploy commander-kuma-web-service \
    --image=$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest \
    --region=$REGION \
    --execution-environment=gen2 \
    --add-volume=name=kuma-data,type=cloud-storage,bucket=$BUCKET_NAME \
    --add-volume-mount=volume=kuma-data,mount-path=/work \
    --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash" \
    --allow-unauthenticated

# マウント確認(デプロイ後、/work が GCS になっているか): サンプルで解析→保存→別ブラウザで ?piece= が開ければOK。
