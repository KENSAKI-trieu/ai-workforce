# AI Workforce

AI Workforce là nền tảng quản trị doanh nghiệp đa tenant, kết hợp các mô-đun nghiệp vụ với trợ lý AI có kiểm soát. Hệ thống tập trung vào quản trị nhân sự, vận hành workflow, truy xuất tri thức nội bộ và thực thi công cụ theo quyền của người dùng.

## Chức năng chính

- Quản lý tổ chức, người dùng, vai trò và phân quyền theo tenant.
- Trợ lý AI chuyên biệt cho HR và các nghiệp vụ doanh nghiệp.
- Phân loại intent để định tuyến câu hỏi sang truy vấn dữ liệu, tìm kiếm tài liệu hoặc yêu cầu làm rõ.
- Tìm kiếm tri thức bằng PostgreSQL/pgvector và cơ chế RAG lai.
- Workflow có bước phê duyệt thủ công cho các thao tác nhạy cảm.
- Theo dõi audit log, phiên làm việc, mức sử dụng và chi phí AI.
- Xuất danh sách nhân sự sang Excel, PDF hoặc JSON theo phạm vi dữ liệu được cấp quyền.

## Kiến trúc

| Thành phần | Vai trò | Cổng mặc định |
| --- | --- | --- |
| `frontend` | Giao diện Next.js | `3000` |
| `backend` | API nghiệp vụ FastAPI | `8000` |
| `worker` | Xử lý tác vụ nền | Không công khai |
| `ai-service` | Embedding, reranking và suy luận AI | `8100` |
| `postgres` | Dữ liệu nghiệp vụ và vector | `5432` |
| `redis` | Hàng đợi và dữ liệu tạm | `6379` |

Luồng xử lý chính:

```text
Browser -> Frontend -> Backend API -> PostgreSQL / Redis
                              |
                              +----> AI Service / LLM providers
```

Backend chịu trách nhiệm xác thực, phân quyền, kiểm soát công cụ và cô lập dữ liệu tenant. AI Service chỉ đảm nhiệm các tác vụ mô hình; các quyết định truy cập dữ liệu vẫn được thực thi tại Backend.

## Công nghệ

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS, TanStack Query, Zustand.
- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic, Pydantic.
- AI: OpenAI-compatible providers, Hugging Face, sentence-transformers và cơ chế RAG.
- Dữ liệu: PostgreSQL, pgvector, Redis.
- Hạ tầng: Docker Compose, GitHub Actions, GitHub Container Registry.

## Kiến trúc AI

### Phân tách trách nhiệm

Hệ thống tách AI runtime khỏi lớp nghiệp vụ để mô hình không trực tiếp nắm quyền truy cập dữ liệu:

| Lớp | Trách nhiệm |
| --- | --- |
| Backend | Xác thực, tenant isolation, RBAC, lọc ACL tài liệu, truy vấn PostgreSQL, thực thi tool, approval, audit và cost tracking. |
| AI Service | Chunking, token counting, embedding, reranking, chọn agent và gọi LLM provider. Dịch vụ này stateless và không có database credential. |

Backend giao tiếp với AI Service qua HTTP nội bộ và header `X-AI-Service-Key`. Khi `AI_SERVICE_URL` để trống, Backend dùng implementation in-process để phát triển và chạy test. Khi URL đã được cấu hình, lỗi từ AI Service được trả về rõ ràng; Backend không tự đổi embedding space bằng một model fallback khác.

Các endpoint chính của AI Service:

| Endpoint | Chức năng |
| --- | --- |
| `POST /v1/rag/chunk` | Semantic chunking cho nội dung đã parse. |
| `POST /v1/token-count` | Đếm token bằng tokenizer của embedding model. |
| `POST /v1/embeddings` | Sinh document/query embedding và trả model, version, dimension. |
| `POST /v1/rag/rerank` | Cross-encoder reranking, score fusion và metadata latency/fallback. |
| `POST /v1/agents/route` | Chọn agent theo role được yêu cầu hoặc keyword routing. |
| `POST /v1/llm/generate` | Gọi OpenAI, Gemini hoặc local provider qua interface thống nhất. |
| `GET /health/accelerator` | Báo trạng thái CUDA, PyTorch và thiết bị model đang cấu hình. |

### Agent và intent routing

Luồng HR hiện dùng bộ phân loại intent xác định, không dùng một learned classifier:

1. Chuẩn hóa Unicode, chuyển chữ thường, loại dấu và chuẩn hóa khoảng trắng.
2. Nhận diện kết hợp giữa hành động và thực thể, ví dụ `tìm/liệt kê/bao nhiêu` + `nhân viên/quản lý`.
3. Ưu tiên intent cụ thể như hồ sơ cá nhân, hợp đồng, phép, xuất file và danh sách quản lý trước intent chính sách tổng quát.
4. Chỉ các câu có marker thông tin như `chính sách`, `quy định`, `thủ tục`, `cách`, `điều kiện` mới đi vào RAG.
5. Intent chưa xác định trả yêu cầu làm rõ thay vì mặc định tìm trong kho tài liệu.

Cách định tuyến này giúp câu hỏi dữ liệu có cấu trúc, chẳng hạn “có bao nhiêu nhân viên” hoặc “tìm các nhân viên quản lý”, gọi đúng tool SQL thay vì bị chuyển sang policy RAG. Mỗi tool tiếp tục được kiểm tra với allowlist của agent và quyền người dùng trước khi thực thi.

Agent registry trong AI Service cũng có keyword router để chọn nhóm agent khi caller không truyền role. Đây là fallback routing đơn giản; chưa phải semantic router hoặc LLM router được huấn luyện. Nếu thay bằng classifier, nên giữ lớp deterministic guardrail cho các intent có side effect và version hóa classifier cùng bộ evaluation riêng.

### Trạng thái tích hợp LLM

AI Service đã có provider abstraction cho OpenAI, Gemini và local provider. Tuy nhiên, luồng nghiệp vụ hiện tại chủ yếu được điều phối tại Backend: tool cho dữ liệu có cấu trúc trả payload xác định, còn RAG trả grounded excerpt và citation từ chunk tốt nhất. Endpoint sinh văn bản tổng quát đã sẵn sàng nhưng chưa phải bước bắt buộc trong mọi phản hồi agent.

Thiết kế này cố ý tách retrieval/tool correctness khỏi tính biến thiên của LLM. Khi bổ sung generative synthesis, context và citation nên được giữ dưới dạng dữ liệu có cấu trúc, sau đó kiểm tra citation coverage và faithfulness trước khi trả kết quả.

## RAG pipeline

### Ingestion và indexing

```text
PDF / DOCX / TXT / MD / CSV / public HTML
        |
        v
Parse -> conservative cleaning -> semantic sections -> token windows
        |
        v
SHA-256 deduplication -> embedding text -> batched embedding
        |
        v
PostgreSQL metadata + pgvector HNSW + PostgreSQL FTS GIN
```

Pipeline thực hiện các bước sau:

1. Backend lưu file gốc theo đường dẫn cô lập tenant và ghi SHA-256 của source.
2. Parser giữ page marker của PDF và heading của DOCX/Markdown; URL công khai được tải và chuyển HTML thành text tại Backend. OCR chưa nằm trong ingestion path hiện tại.
3. Cleaner chỉ loại control character, khoảng trắng thừa, footer trang phổ biến và nối từ bị ngắt dòng. Các cấu trúc nghiệp vụ như Điều, Khoản, Bước, Trách nhiệm và Điều kiện được giữ lại.
4. Chunker ưu tiên ranh giới semantic: Markdown heading, numbered heading, Điều, Khoản, Bước, Trách nhiệm, Điều kiện và Phụ lục. Section chỉ được chia tiếp khi vượt giới hạn token.
5. Cấu hình mặc định là target `450`, max `700` và overlap `80` token. Page range, header path, section type và token count được gắn vào từng chunk.
6. `embedding_text` được tạo từ phòng ban, loại tài liệu, tên tài liệu, section title và nội dung. Tenant, ACL, trạng thái và ngày hiệu lực không được đưa vào vector; chúng chỉ dùng làm database filter.
7. Chunk trùng trong cùng lần ingest được loại bằng SHA-256. Vector cũ được tái sử dụng khi `content_hash`, `embedding_text`, model và version không đổi.
8. Embedding được chạy theo batch, retry exponential, kiểm tra input-token limit, số vector và dimension trước khi index.
9. Sau khi embedding hoàn tất, Backend khóa record của đúng document version và thay batch chunk trong một transaction ngắn. Version cũ được chuyển sang `inactive` khi version mới được kích hoạt.

Trạng thái xử lý tài liệu đi qua `uploaded/parsing -> chunking -> embedding -> indexing -> ready`; lỗi được lưu ở trạng thái `failed` để phục vụ retry và audit.

### Embedding

Backend và AI Service dùng chung contract gồm `model`, `version`, `dimension` và `input_type`:

- Docker Compose mặc định dùng `Qwen/Qwen3-Embedding-0.6B`, vector `1024` chiều, L2 normalization và cosine distance.
- Query được thêm retrieval instruction trước khi embed; document embedding dùng metadata-aware text.
- Chỉ chunk có đúng `embedding_model` và `embedding_version` hiện tại mới tham gia dense retrieval. Điều này ngăn trộn vector từ các embedding space khác nhau.
- Provider hỗ trợ Hugging Face/sentence-transformers, OpenAI embedding và deterministic hash embedding.
- Deterministic embedding chỉ phục vụ local test và môi trường không tải model; không nên dùng để đánh giá semantic retrieval trong production.
- HNSW index dùng `vector_cosine_ops`. PostgreSQL vẫn giữ cột legacy `1536` chiều để đọc dữ liệu cũ trong giai đoạn chuyển đổi.

Khi đổi model hoặc dimension, cần tạo embedding version mới và re-index tài liệu. Không nên cập nhật tên model trên cấu hình rồi tiếp tục dùng index cũ.

### Retrieval và governance filtering

Governance filter được áp dụng trước khi candidate content rời Backend:

- `tenant_id` bắt buộc.
- `status=active` và khoảng `effective_date/expiration_date` hợp lệ tại thời điểm truy vấn.
- Phạm vi phòng ban và collection/document/chunk được gán cho agent.
- `allowed_roles` và `confidentiality`; tài liệu `restricted` yêu cầu role phù hợp, trừ các role đặc quyền đã định nghĩa.

Sau bước lọc, retrieval chạy hai nhánh độc lập:

| Nhánh | Cách xếp hạng | Candidate mặc định |
| --- | --- | --- |
| Dense | pgvector cosine distance trên embedding đúng model/version | Top 30 |
| Sparse | PostgreSQL `to_tsvector('simple', content)` + `plainto_tsquery` + `ts_rank_cd` | Top 30 |

Hai danh sách được hợp nhất bằng Reciprocal Rank Fusion:

```text
RRF(d) = sum(1 / (60 + rank_m(d)))
```

RRF score, dense similarity và sparse score được chuẩn hóa rồi chuyển sang reranker. Nếu indexed query không khả dụng trong chế độ in-process, Backend có fallback tính cosine và lexical overlap trực tiếp trên candidate đã được cấp quyền; fallback này ưu tiên khả năng phát triển/test, không phù hợp với corpus lớn.

### Reranking và relevance gate

Trong cấu hình model, tối đa 30 candidate đã khử trùng lặp được đưa vào `BAAI/bge-reranker-v2-m3` qua `sentence-transformers` `CrossEncoder`:

- Input của cross-encoder gồm query và document text có metadata `document title`, `section`, `department`, `document type`.
- Raw logit được đưa qua sigmoid về miền `0..1`.
- Final score mặc định: `0.90 * model_score + 0.10 * retrieval_prior`.
- Retrieval prior: `0.65 * dense + 0.20 * sparse + 0.15 * RRF`.
- Candidate có model score thấp hơn `RERANK_MIN_MODEL_SCORE` bị loại, sau đó trả Top-K.
- Model được lazy-load; inference được serialize trong mỗi process để kiểm soát VRAM. Khi CUDA OOM, batch size được giảm dần đến `1`.
- Nếu cross-encoder lỗi và `RERANK_FALLBACK_ENABLED=true`, AI Service chuyển sang lexical reranker và đánh dấu `fallback_used` trong response metadata.

Khi AI Service không được cấu hình, Backend dùng relevance gate nội bộ. Với embedding thật, score kết hợp dense, lexical coverage, RRF và sparse; với deterministic embedding, lexical score được ưu tiên. Hai ngưỡng `RAG_MIN_DENSE_SCORE` và `RAG_MIN_RELEVANCE_SCORE` ngăn hệ thống luôn trả một “kết quả tốt nhất” cho câu hỏi ngoài miền.

### Grounding và citation

Mỗi kết quả retrieval mang theo document ID/title, version, section, page range, chunk ID, model/version và governance metadata. Citation có dạng:

```text
[Citation: <document>, v<version>, <section>, p. <page>; chunk=<uuid>]
```

Nếu không có chunk vượt relevance gate, agent trả thông báo không tìm thấy tài liệu phù hợp thay vì suy diễn chính sách. Luồng hiện tại ưu tiên grounded excerpt; chưa thực hiện claim-level citation verification cho câu trả lời do LLM tổng hợp.

### Đánh giá và observability

Repository có các primitive evaluation cho `Recall@K`, reciprocal rank/MRR, `NDCG@K` và lexical faithfulness. Rerank API trả thêm backend, model, số candidate, latency và cờ fallback; accelerator health endpoint cung cấp thông tin CUDA runtime.

Đây mới là lớp đo lường nền tảng. Trước production, nên bổ sung versioned golden set theo tenant/domain, negative và out-of-domain queries, các lát cắt theo ngôn ngữ/phòng ban/quyền truy cập, cùng quality gate cho retrieval, reranking, citation coverage, faithfulness, latency và chi phí.

## Yêu cầu môi trường

Cách đơn giản nhất để chạy toàn bộ hệ thống là dùng Docker Desktop hoặc Docker Engine có Docker Compose.

Để phát triển từng dịch vụ trực tiếp trên máy, cần:

- Python 3.11.
- Node.js 22 và npm.
- PostgreSQL có extension `pgvector`.
- Redis 7.

File [`docker-compose.yml`](docker-compose.yml) hiện cấu hình AI Service sử dụng NVIDIA GPU. Với máy chỉ có CPU, cần bỏ phần GPU reservation và đặt các biến thiết bị của AI Service thành `cpu`.

## Khởi chạy bằng Docker Compose

1. Tạo file cấu hình Backend:

   ```bash
   cp backend/.env.example backend/.env
   ```

   Trên PowerShell:

   ```powershell
   Copy-Item backend/.env.example backend/.env
   ```

2. Cập nhật tối thiểu `POSTGRES_PASSWORD` và `SECRET_KEY` trong `backend/.env`.

   Nếu bật xác thực nội bộ cho AI Service, tạo file `.env` tại thư mục gốc và đặt `AI_SERVICE_INTERNAL_TOKEN`. Docker Compose sẽ truyền cùng token này cho Backend, Worker và AI Service.

3. Khởi động hệ thống:

   ```bash
   docker compose up --build
   ```

4. Truy cập các địa chỉ:

   - Frontend: <http://localhost:3000>
   - API documentation: <http://localhost:8000/docs>
   - Backend health check: <http://localhost:8000/health>
   - AI Service health check: <http://localhost:8100/health>

Migration cơ sở dữ liệu được áp dụng khi container Backend khởi động.

## Phát triển cục bộ

### Backend

Backend cần PostgreSQL và Redis đang hoạt động trước khi khởi chạy.

```bash
cd backend
python -m venv .venv
python -m pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

Worker ở terminal thứ hai chỉ cần cho các workflow hỗ trợ khách hàng chạy nền:

```bash
cd backend
python -m app.worker
```

Upload tài liệu không phụ thuộc worker hay Redis. Backend xử lý trực tiếp và commit
checkpoint sau từng stage: `uploaded -> parsed -> chunked -> embedded -> ready`.
Nếu tiến trình dừng giữa một stage, trang Knowledge sẽ lấy bản ghi dang dở và tiếp tục
từ checkpoint trước đó. Ví dụ, dừng giữa embedding sẽ giữ nguyên chunks đã lưu và tạo
lại toàn bộ embedding từ checkpoint `chunked`.

Kích hoạt virtual environment trước khi cài dependency:

- PowerShell: `.venv\Scripts\Activate.ps1`
- Linux/macOS: `source .venv/bin/activate`

### AI Service

```bash
cd apps/ai-service
python -m venv .venv
python -m pip install -e ".[test]"
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8100
```

Để chạy embedding và reranking bằng model Hugging Face cục bộ, cài thêm extra `huggingface`:

```bash
python -m pip install -e ".[huggingface,test]"
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Frontend mặc định gọi Backend qua `NEXT_PUBLIC_API_URL=http://localhost:8000`. Có thể thay đổi giá trị này trong `frontend/.env.local`.

## Cấu hình

Các mẫu cấu hình nằm tại:

- [`backend/.env.example`](backend/.env.example)
- [`apps/ai-service/.env.example`](apps/ai-service/.env.example)

Những nhóm biến quan trọng gồm:

| Nhóm | Biến tiêu biểu |
| --- | --- |
| Cơ sở dữ liệu | `DATABASE_URL`, `POSTGRES_*` |
| Xác thực | `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES` |
| AI providers | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_AI_API_KEY` |
| AI Service | `AI_SERVICE_URL`, `AI_SERVICE_INTERNAL_TOKEN`, `AI_SERVICE_TIMEOUT_SECONDS` |
| Embedding | `EMBEDDING_BACKEND`, `EMBEDDING_MODEL_NAME`, `EMBEDDING_VERSION`, `EMBEDDING_DIMENSION` |
| RAG | `RAG_CHUNK_*`, `RAG_MIN_DENSE_SCORE`, `RAG_MIN_RELEVANCE_SCORE` |
| Reranking | `RERANK_BACKEND`, `RERANK_MODEL_NAME`, `RERANK_MODEL_WEIGHT`, `RERANK_MIN_MODEL_SCORE` |
| LangChain migration | `LANGCHAIN_ENABLED`, `LANGGRAPH_ENABLED`, `LANGCHAIN_AGENT_ROLES` |
| Hàng đợi | `REDIS_URL`, `REDIS_QUEUE_NAME` |
| Email và lưu trữ | `SMTP_*`, `MINIO_*`, `CLOUDINARY_*` |

Không commit file `.env`, API key, mật khẩu hoặc token vào repository. Khi triển khai production, nên quản lý secret bằng cơ chế của nền tảng triển khai.

## Migration cơ sở dữ liệu

Áp dụng migration mới nhất:

```bash
cd backend
alembic upgrade head
```

Kiểm tra revision hiện tại:

```bash
alembic current
```

## Kiểm thử và kiểm tra chất lượng

Backend:

```bash
cd backend
python -m pytest -q
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

Pipeline CI thực hiện migration trên cơ sở dữ liệu sạch, chạy test Backend, lint/build Frontend và kiểm tra khả năng build các container chính.

## Mô hình bảo mật

- Mọi truy vấn dữ liệu nghiệp vụ phải được giới hạn theo tenant hiện tại.
- Quyền người dùng được kiểm tra trước khi công cụ AI được phép thực thi.
- Các thao tác nhạy cảm có thể yêu cầu phê duyệt thủ công.
- Dữ liệu xuất file tuân theo phạm vi quyền và được ghi audit log.
- Kết quả từ tài liệu nội bộ không được dùng để vượt qua chính sách truy cập dữ liệu có cấu trúc.

Các cơ chế trên hỗ trợ kiểm soát rủi ro nhưng không thay thế việc rà soát cấu hình, secret, hạ tầng và chính sách bảo mật trước khi triển khai production.

## Cấu trúc repository

```text
.
├── apps/ai-service/       # Dịch vụ AI độc lập
├── backend/               # API, worker, migration và test
├── frontend/              # Ứng dụng web Next.js
├── docs/                  # Tài liệu kiến trúc, API và triển khai
├── docker-compose.yml     # Môi trường chạy tích hợp
└── .github/workflows/     # Pipeline CI/CD
```

## Tài liệu bổ sung

- [Tổng quan tài liệu](docs/README.md)
- [Kiến trúc hệ thống](docs/03-system-architecture.md)
- [Thiết kế agent](docs/05-agent-design.md)
- [Thiết kế API](docs/09-api-design.md)
- [Hướng dẫn triển khai](docs/11-deployment.md)
- [Kiến trúc AI Service](docs/AI_SERVICE_ARCHITECTURE.md)
- [Streaming và giao diện — Giai đoạn 7](docs/21-streaming-ui-phase-7.md)
