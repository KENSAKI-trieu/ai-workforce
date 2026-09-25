# AI Workforce

## User Manual / Hướng dẫn sử dụng

- [日本語ユーザーマニュアル](docs/manuals/USER_MANUAL_JA.md)
- [Hướng dẫn sử dụng tiếng Việt](docs/manuals/USER_MANUAL_VI.md)

## テスト用メインアカウント

- メールアドレス: `test@gmail.com`
- パスワード: `123456`
- 権限: `CEO`

このアカウントは development/test 環境でのみ使用してください。

AI Workforce は、マルチテナント対応の業務管理機能と AI エージェントを統合したプラットフォームです。人事、ワークフロー、社内文書検索（RAG）、承認、監査ログ、AI 利用コスト管理などを提供します。

## システム構成

| コンポーネント | 技術 / 役割 | 既定ポート |
| --- | --- | --- |
| `frontend` | Next.js 16 / React 19 の Web UI | `3000` |
| `backend` | FastAPI の業務 API | `8000` |
| `worker` | Redis キューのバックグラウンド処理 | なし |
| `ai-service` | LLM、Embedding、Reranking、RAG | `8100` |
| `postgres` | PostgreSQL + pgvector | `5432` |
| `redis` | キューおよび一時データ | `6379` |

```text
Browser -> Frontend -> Backend -> PostgreSQL / Redis
                              -> AI Service -> OpenAI / Gemini（任意）
                                           -> Hugging Face モデル
```

## 必要環境

最も簡単な方法は Docker Compose を使用することです。

- Docker Desktop、または Docker Engine + Docker Compose v2
- NVIDIA GPU を使用する場合: NVIDIA Driver と NVIDIA Container Toolkit
- ローカル開発の場合:
  - Python 3.11
  - Node.js 22 と npm
  - PostgreSQL（`pgvector` extension を含む）
  - Redis 7

## 必要な AI モデルと外部 API

現在のテスト環境では、Embedding は Gemini API、Reranking は Jina の外部 API を使用します。

| 用途 | モデル | 設定 |
| --- | --- | --- |
| Embedding | `gemini-embedding-001` | 768 次元、Gemini API |
| Reranking | `jina-reranker-v3.5` | Jina Rerank API |

現在の Reranking 設定は `RERANK_BACKEND=jina` です。候補文書は AI Service から Jina API に送信され、返されたスコアで並べ替えられます。`BAAI/bge-reranker-v2-m3` はローカル実行へ切り替える場合の代替モデルであり、現在の Reranking では使用していません。

Embedding は AI Service から Gemini API に送信されます。`GOOGLE_AI_API_KEY` は `.env` またはシークレット管理基盤にのみ保存し、Git にコミットしないでください。

チャット LLM はローカルへのインストールが不要です。必要に応じて API キーを設定します。

- OpenAI: 既定値 `gpt-4o-mini`
- Google Gemini: 既定値 `gemini-3.6-flash`
- API キーを設定しない場合: 開発・テスト用の `local-deterministic` provider

`local-deterministic` は疎通確認用であり、本番向けの生成 AI モデルではありません。

## Docker Compose でセットアップする（推奨）

### 1. Backend の環境変数を作成する

PowerShell:

```powershell
Copy-Item backend/.env.example backend/.env
```

Linux / macOS:

```bash
cp backend/.env.example backend/.env
```

`backend/.env` を開き、最低限、次の値を設定してください。

```dotenv
POSTGRES_PASSWORD=十分に強いデータベースパスワード
SECRET_KEY=十分に長いランダム文字列
SEED_DEFAULT_PASSWORD=デモユーザー用の強いパスワード
```

`DATABASE_URL` は空欄のままで構いません。`POSTGRES_*` から自動生成されます。

### 2. Compose 用のルート環境変数を作成する

プロジェクトのルートに `.env` を作成します。これは `backend/.env` とは別のファイルです。Compose の `${...}` 展開に使用されます。

Gemini Embedding を使用する例:

```dotenv
AI_SERVICE_INTERNAL_TOKEN=十分に長いランダムな内部トークン

AI_EMBEDDING_BACKEND=gemini
AI_EMBEDDING_MODEL_NAME=gemini-embedding-001
AI_EMBEDDING_VERSION=gemini-embedding-001-v1
AI_EMBEDDING_DIMENSION=768

AI_RERANK_BACKEND=jina
JINA_RERANK_MODEL=jina-reranker-v3.5
JINA_API_KEY=Jina の API キー

# 生成 AI を使用する場合のみ設定する
OPENAI_API_KEY=
GOOGLE_AI_API_KEY=Gemini の API キー
OPENAI_CHAT_MODEL=gpt-4o-mini
GEMINI_CHAT_MODEL=gemini-3.6-flash
```

`.env`、API キー、パスワード、token は Git にコミットしないでください。

### 3. ローカル GPU を使用しない場合

Gemini Embedding と Jina Rerank は外部 API のため、Embedding 用のローカル GPU は不要です。ローカル BGE reranker に切り替える場合のみ、次を設定します。

```dotenv
AI_RERANK_DEVICE=cpu
AI_RERANK_DTYPE=float32
```

Gemini/Jina のみを使用する場合は、`docker-compose.yml` の `ai-service` にある次の GPU reservation を削除、またはコメントアウトしてください。

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

Gemini Embedding と Jina Rerank の処理速度は、ローカル GPU の有無に依存しません。

### 4. ビルドして起動する

```bash
docker compose up --build -d
```

初回ビルドでは Python、PyTorch、Node.js パッケージをインストールするため時間がかかります。

状態確認:

```bash
docker compose ps
docker compose logs -f ai-service backend worker
```

### 5. Embedding API を確認する

Gemini Embedding はモデルの事前ダウンロードを必要としません。`GOOGLE_AI_API_KEY` を設定し、AI Service から Gemini API への外向き HTTPS 通信を許可してください。`AI_RERANK_LOCAL_FILES_ONLY=true` はローカル BGE を使用する場合にのみ必要です。

```bash
docker compose up -d
```

### 6. データベースとデモデータ

Backend コンテナの起動時に Alembic migration が自動実行されます。デモ会社、ユーザー、エージェント、サンプルデータも作成する場合は、次を実行します。

```bash
docker compose exec backend python -m app.db.init_db
```

デモ管理者のメールアドレスは `admin@company.com`、パスワードは事前に `backend/.env` の `SEED_DEFAULT_PASSWORD` に設定した値です。本番環境ではサンプルデータを投入しないでください。

### 7. アクセス先

- Frontend: <http://localhost:3000>
- Backend API ドキュメント: <http://localhost:8000/docs>
- Backend health: <http://localhost:8000/health>
- AI Service health: <http://localhost:8100/health>
- Accelerator 情報: `GET http://localhost:8100/health/accelerator`（内部 token を設定した場合は header が必要）

ヘルスチェック例:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8100/health
Invoke-RestMethod http://localhost:8100/health/accelerator -Headers @{ "X-AI-Service-Key" = "ルート .env の AI_SERVICE_INTERNAL_TOKEN" }
```

## 各サービスをローカルで実行する

### 1. PostgreSQL と Redis

ローカルに PostgreSQL / Redis がない場合は、インフラのみ Docker で起動できます。

```bash
docker compose up -d postgres redis
```

### 2. Backend

PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

Linux / macOS では、virtual environment の有効化と env コピーを次のように置き換えます。

```bash
source .venv/bin/activate
cp .env.example .env
```

バックグラウンド処理が必要な場合は、別の terminal で worker を起動します。

```bash
cd backend
python -m app.worker
```

### 3. AI Service とモデル

```powershell
cd apps/ai-service
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[huggingface,test]"
Copy-Item .env.example .env
```

`apps/ai-service/.env` を次のように設定します。

```dotenv
EMBEDDING_BACKEND=gemini
EMBEDDING_MODEL_NAME=gemini-embedding-001
EMBEDDING_VERSION=gemini-embedding-001-v1
EMBEDDING_DIMENSION=768
GOOGLE_AI_API_KEY=Gemini の API キー
RERANK_BACKEND=jina
JINA_RERANK_MODEL=jina-reranker-v3.5
JINA_API_KEY=Jina の API キー
```

AI Service を起動します。

```powershell
python -m uvicorn app.main:app --reload --port 8100
```

Backend から独立した AI Service を使用する場合は、`backend/.env` に次を設定します。

```dotenv
AI_SERVICE_URL=http://localhost:8100
AI_SERVICE_INTERNAL_TOKEN=AI Service 側と同じ token
```

### 4. Frontend

```bash
cd frontend
npm ci
npm run dev
```

必要に応じて `frontend/.env.local` を作成します。

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## よく使うコマンド

```bash
# 全サービスを停止する（データ volume は保持）
docker compose down

# 再ビルドする
docker compose up --build -d

# Backend test
cd backend
python -m pytest -q

# AI Service test
cd apps/ai-service
python -m pytest -q

# Frontend lint / build
cd frontend
npm run lint
npm run build
```

`docker compose down -v` は PostgreSQL データとダウンロード済みモデルの volume も削除します。データを保持したい場合は `-v` を付けないでください。

## トラブルシューティング

### `PyTorch cannot access an NVIDIA GPU`

- `nvidia-smi` で GPU が認識されているか確認します。
- NVIDIA Container Toolkit が設定されているか確認します。
- Gemini/Jina のみを使用する環境では GPU reservation を削除します。このエラーはローカル BGE を選択した場合にのみ関係します。

### Gemini Embedding API に接続できない

- `GOOGLE_AI_API_KEY`、インターネット接続、proxy、firewall を確認します。
- Backend と AI Service の model/version/dimension が一致していることを確認します。

### Embedding の dimension エラー

`gemini-embedding-001` はこのプロジェクトで 768 次元を使用します。次の設定を Backend と AI Service で一致させてください。

```dotenv
EMBEDDING_MODEL_NAME=gemini-embedding-001
EMBEDDING_VERSION=gemini-embedding-001-v1
EMBEDDING_DIMENSION=768
```

モデル、version、dimension を変更した場合は、既存文書を新しい embedding space で再インデックスする必要があります。異なるモデルの vector を同じ index に混在させないでください。

### Backend が起動しない

```bash
docker compose logs backend postgres
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head
```

`POSTGRES_PASSWORD` と `SECRET_KEY` が `backend/.env` に設定されていることを確認してください。

## ディレクトリ構成

```text
.
├── apps/ai-service/   # AI、RAG、Embedding、Reranking
├── backend/           # FastAPI、worker、Alembic、test
├── frontend/          # Next.js Web UI
├── docs/              # 設計・運用ドキュメント
└── docker-compose.yml
```

詳細は [docs/README.md](docs/README.md) と [apps/ai-service/README.md](apps/ai-service/README.md) を参照してください。
