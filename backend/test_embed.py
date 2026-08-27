"""Quick test: embedding service + full upload pipeline."""
import sys, traceback, os
sys.path.insert(0, ".")

# 1. Test embedding service config
print("=" * 60)
print("1. EMBEDDING SERVICE CONFIG")
print("=" * 60)
from app.services.embedding_service import get_embedding_service
svc = get_embedding_service()
print(f"  backend    : {svc.backend}")
print(f"  model_name : {svc.model_name}")
print(f"  batch_size : {svc.batch_size}")
print(f"  dimension  : {svc.dimension}")
print(f"  max_retries: {svc.max_retries}")

# 2. Test AI service client
print()
print("=" * 60)
print("2. AI SERVICE CLIENT")
print("=" * 60)
from app.services.ai_service_client import get_ai_service_client
client = get_ai_service_client()
print(f"  enabled    : {client.enabled}")
print(f"  base_url   : {client.base_url}")
print(f"  timeout    : {client.timeout}s")

# 3. Test actual embed call
print()
print("=" * 60)
print("3. EMBED TEST (1 chunk)")
print("=" * 60)
try:
    vectors = svc.embed_texts(["Day la noi dung test chunk tu README.md cua du an AI Workforce."])
    print(f"  OK - got {len(vectors)} vector(s), dim={len(vectors[0])}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()

# 4. Test full ingestion pipeline
print()
print("=" * 60)
print("4. FULL INGESTION PIPELINE TEST")
print("=" * 60)
README_PATH = r"C:\Users\admin\Downloads\code_ai\AI-workforce\README.md"
try:
    with open(README_PATH, "rb") as f:
        data = f.read()
    print(f"  File size  : {len(data)} bytes")

    from app.services.document_parser import extract_file_text
    text = extract_file_text("README.md", data).strip()
    print(f"  Parsed text: {len(text)} chars, {len(text.split())} words")

    from app.services.rag_service import chunk_document_content
    chunks = chunk_document_content(text)
    print(f"  Chunks     : {len(chunks)}")

    print()
    print("  Embedding each chunk (batch_size=1)...")
    import time
    t0 = time.perf_counter()
    from app.services.embedding_service import build_embedding_text
    for i, chunk in enumerate(chunks):
        embedding_text = build_embedding_text({
            "department": "ALL",
            "document_type": "knowledge",
            "document_title": "README.md",
            "section_title": chunk.get("section_title", ""),
            "content": chunk["content"],
        })
        vecs = svc.embed_texts([embedding_text])
        elapsed = time.perf_counter() - t0
        print(f"  Chunk {i+1:3d}/{len(chunks)}: {len(chunk['content'])} chars -> dim={len(vecs[0])} ({elapsed:.1f}s total)")

    total = time.perf_counter() - t0
    print(f"\n  TOTAL EMBED TIME: {total:.1f}s for {len(chunks)} chunks")
    print(f"  Avg per chunk   : {total/len(chunks):.2f}s")

except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
