# AI Workforce AI Service

Stateless service that owns AI-specific computation: semantic chunking, embeddings,
reranking, prompt loading, provider routing, agent metadata and guardrails.

The business backend remains the authority for tenants, ACL, database persistence,
audit logs and tool execution. It sends only the minimum authorized payload to this
service over the internal network.

## Local run

```powershell
cd apps/ai-service
python -m pip install -e ".[test]"
uvicorn app.main:app --reload --port 8100
```

Install the local Hugging Face model runtime with:

```powershell
python -m pip install -e ".[huggingface,test]"
```

Main internal endpoints:

- `POST /v1/rag/chunk`
- `POST /v1/embeddings`
- `POST /v1/token-count`
- `POST /v1/rag/rerank`
- `POST /v1/agents/route`
- `POST /v1/llm/generate`

Set `AI_SERVICE_INTERNAL_TOKEN` in both services outside local development.

## LangChain model layer

OpenAI and Gemini chat calls use LangChain chat models. Configure
`OPENAI_API_KEY` and/or `GOOGLE_AI_API_KEY`; calls retry according to
`LLM_MAX_RETRIES`, time out after `LLM_TIMEOUT_SECONDS`, then fall through to
the other configured provider and finally the deterministic local provider.
An explicit model override applies only to the primary provider.

The `app/chains` package contains chat, grounded RAG answer and structured
extraction chains. Routing decisions, citations and contract findings are
validated with the Pydantic models under `app/schemas`. Set
`LANGCHAIN_ENABLED=true` to enable structured model routing; the registry route
remains the fallback when no external model is configured or all calls fail.

### HR question/action flow

The Backend sends the HR user's raw message to `POST /v1/llm/generate` for a
strict `QUESTION` or `ACTION` classification. Questions retrieve tenant- and
ACL-filtered HR evidence first, then make a second model call to synthesize the
answer with citations. Actions continue through the governed HR tool router;
an LLM classification can never invent or bypass a tool permission.

Configure `OPENAI_API_KEY` or `GOOGLE_AI_API_KEY` to enable model classification
and answer generation. Docker Compose reads these values from `backend/.env`;
when starting AI Service directly, place the value in `apps/ai-service/.env` or
export it in the process environment. `JINA_API_KEY` only powers hosted
reranking and is not a chat-generation credential. Without a chat model
credential, the HR flow safely falls back to the existing deterministic router
and retrieved answer.

## BGE reranking

Docker enables `BAAI/bge-reranker-v2-m3` by default (`RERANK_BACKEND=bge`). The model is lazy-loaded on
the first rerank request and stored in the shared Hugging Face model volume. The
pipeline deduplicates and caps hybrid candidates, scores query/document pairs in
batches, converts logits to 0-1 probabilities, fuses a small retrieval prior and
returns the configured `top_k` results.

Use `RERANK_BACKEND=lexical` for lightweight local tests. If BGE inference fails,
`RERANK_FALLBACK_ENABLED=true` keeps retrieval available and marks
`fallback_used=true` in the API response.

Docker is configured for one NVIDIA GPU with CUDA 12.4 PyTorch wheels. The RTX
3060 6 GB profile uses FP16, embedding batch 8, rerank batch 2 and a 2048-token
rerank window. Check the active runtime with `GET /health/accelerator`. Reranking
automatically retries CUDA out-of-memory errors with a smaller batch before using
the configured fallback.

## Hosted Jina reranking

To avoid loading a reranker into local RAM or VRAM, keep embeddings local and
route reranking through Jina AI:

```dotenv
RERANK_BACKEND=jina
JINA_API_KEY=your-key
JINA_RERANK_URL=https://api.jina.ai/v1/rerank
JINA_RERANK_MODEL=jina-reranker-v3.5
JINA_RERANK_TIMEOUT_SECONDS=30
JINA_RERANK_SCORE_NORMALIZATION=auto
RERANK_FALLBACK_ENABLED=true
```

Candidate text is sent to Jina for scoring. Keep the API key only in `.env` and
do not commit it. If the hosted request fails, lexical fallback remains active.
The default `auto` score normalization applies sigmoid conversion to Jina v3
logits while leaving the already normalized v2 scores unchanged.

run:
cd C:\Users\admin\Downloads\code_ai\AI-workforce\apps\ai-service
python -m uvicorn app.main:app --reload --port 8100
