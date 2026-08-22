# AI Workforce

## Tài khoản chính để test

- Tài khoản: `test@gmail.com`
- Mật khẩu: `123456`
- Quyền: `CEO`

Chỉ sử dụng tài khoản này trong môi trường development/test.

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

## 必要な AI モデル

Docker Compose の既定構成では、次の公開 Hugging Face モデルを使用します。

| 用途 | モデル | 設定 |
| --- | --- | --- |
| Embedding | `Qwen/Qwen3-Embedding-0.6B` | 1024 次元、`sentence-transformers` |
| Reranking | `BAAI/bge-reranker-v2-m3` | `CrossEncoder` |

これらのモデルは初回リクエスト時に自動ダウンロードされます。Docker では `hf_models` volume に保存されるため、コンテナを再作成しても通常は再ダウンロードされません。

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

NVIDIA GPU を使用する例:

```dotenv
AI_SERVICE_INTERNAL_TOKEN=十分に長いランダムな内部トークン

AI_EMBEDDING_BACKEND=sentence_transformers
AI_EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B
AI_EMBEDDING_VERSION=qwen3-embedding-v1
AI_EMBEDDING_DIMENSION=1024
AI_EMBEDDING_DEVICE=cuda
AI_EMBEDDING_DTYPE=float16

AI_RERANK_BACKEND=bge
AI_RERANK_MODEL_NAME=BAAI/bge-reranker-v2-m3
AI_RERANK_DEVICE=cuda
AI_RERANK_DTYPE=float16

# 生成 AI を使用する場合のみ設定する
OPENAI_API_KEY=
GOOGLE_AI_API_KEY=
OPENAI_CHAT_MODEL=gpt-4o-mini
GEMINI_CHAT_MODEL=gemini-3.6-flash
```

`.env`、API キー、パスワード、token は Git にコミットしないでください。

### 3. CPU のみで実行する場合

ルート `.env` のデバイスと dtype を次のように変更します。

```dotenv
AI_EMBEDDING_DEVICE=cpu
AI_EMBEDDING_DTYPE=float32
AI_RERANK_DEVICE=cpu
AI_RERANK_DTYPE=float32
```

さらに、`docker-compose.yml` の `ai-service` にある次の GPU reservation を削除、またはコメントアウトしてください。

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

CPU でも動作しますが、Embedding と Reranking は GPU より時間がかかります。

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

### 5. Hugging Face モデルを事前ダウンロードする

事前ダウンロードは任意です。実行しない場合も、最初の Embedding / Reranking リクエスト時に自動ダウンロードされます。

```bash
docker compose exec ai-service python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', cache_folder='/models/huggingface/hub'); CrossEncoder('BAAI/bge-reranker-v2-m3', cache_folder='/models/huggingface/hub', max_length=2048); print('Models downloaded')"
```

モデルキャッシュへの書き込み権限エラーが出た場合は、一度だけ次を実行してから再試行します。

```bash
docker compose run --rm --user root ai-service sh -c "mkdir -p /models/huggingface/hub && chown -R ai:ai /models/huggingface"
```

ダウンロード完了後、オフライン運用する場合はルート `.env` に次を追加して再起動できます。

```dotenv
AI_EMBEDDING_LOCAL_FILES_ONLY=true
AI_RERANK_LOCAL_FILES_ONLY=true
```

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

CPU の場合は `apps/ai-service/.env` を次のように変更します。

```dotenv
EMBEDDING_DEVICE=cpu
EMBEDDING_DTYPE=float32
RERANK_DEVICE=cpu
RERANK_DTYPE=float32
EMBEDDING_CACHE_FOLDER=.cache/huggingface
RERANK_CACHE_FOLDER=.cache/huggingface
```

モデルを事前ダウンロードします。

```powershell
python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', cache_folder='.cache/huggingface'); CrossEncoder('BAAI/bge-reranker-v2-m3', cache_folder='.cache/huggingface', max_length=2048); print('Models downloaded')"
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
- CPU 環境では GPU reservation を削除し、device を `cpu`、dtype を `float32` に変更します。

### モデルをダウンロードできない

- インターネット接続、proxy、firewall を確認します。
- キャッシュディレクトリの書き込み権限を確認します。
- 初回ダウンロードが完了するまでは `*_LOCAL_FILES_ONLY=false` にします。

### Embedding の dimension エラー

`Qwen/Qwen3-Embedding-0.6B` の既定 dimension は `1024` です。次の設定を Backend と AI Service で一致させてください。

```dotenv
EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_VERSION=qwen3-embedding-v1
EMBEDDING_DIMENSION=1024
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
