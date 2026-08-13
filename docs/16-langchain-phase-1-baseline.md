# LangChain migration — Giai đoạn 1

## Phạm vi đã khảo sát

AI Service hiện là dịch vụ FastAPI stateless. Backend giữ quyền kiểm soát tenant,
ACL, database, audit, approval và các thao tác làm thay đổi dữ liệu. Ranh giới này
phải được giữ nguyên trong các giai đoạn LangChain/LangGraph tiếp theo.

Các thành phần AI hiện tại:

- LLM provider gọi OpenAI/Gemini trực tiếp bằng `httpx`, hoặc dùng local provider;
- agent registry định tuyến theo từ khóa;
- chunking, embedding, hybrid retrieval và BGE reranking là implementation riêng;
- backend gọi AI Service cho chunk, embedding, token count và reranking;
- endpoint agent routing và LLM generation đã tồn tại, nhưng trước Giai đoạn 1 chưa
  có method tương ứng trong backend client;
- lớp `backend/app/services/agents/langgraph_engine.py` chỉ là state machine mô
  phỏng và không sử dụng thư viện LangGraph thật.

## Phát hiện chính

1. Registry của AI Service chưa đăng ký `LEGAL` và `KNOWLEDGE`. Hai truy vấn này
   hiện rơi về `CUSTOMER_SUPPORT` nếu đi qua `/v1/agents/route`.
2. Contract test reranking trước đây phụ thuộc khả năng tải model Hugging Face, làm
   test thay đổi theo mạng. Test contract đã được cô lập bằng reranker cố định;
   fallback khi model lỗi vẫn có test riêng.
3. Request agent routing hiện chưa mang `tenant_id`, `user_id`, role và department.
   Do đó canary theo tenant và tool execution có ACL chưa được phép triển khai ở
   Giai đoạn 1. Contract này cần được mở rộng trước khi đưa tools vào LangChain.
4. Không cần chuyển chunking, embedding hoặc reranking sang LangChain trong đợt đầu.

## Feature gates

```env
LANGCHAIN_ENABLED=false
LANGGRAPH_ENABLED=false
LANGCHAIN_AGENT_ROLES=
```

`LANGCHAIN_AGENT_ROLES` nhận danh sách role phân cách bằng dấu phẩy. Giá trị trống
hoặc `*` nghĩa là tất cả role. Trong Giai đoạn 1, flag chỉ thể hiện ý định rollout;
runtime thực tế luôn là `legacy` vì adapter LangChain chưa tồn tại.

Trạng thái có thể kiểm tra qua endpoint nội bộ:

```text
GET /health/runtime
X-AI-Service-Key: <internal token>
```

Nếu flag bị bật nhầm, response vẫn báo `active_runtime=legacy`, `effective=false`
và hệ thống tiếp tục chạy implementation hiện tại.

## Baseline pre-LangChain v1

Dataset gồm 15 ca tổng hợp, không chứa dữ liệu thật và không gọi model bên ngoài:

| Chỉ số | Kết quả |
| --- | ---: |
| Tổng số ca | 15 |
| Quality score tổng hợp | 0.90 |
| Routing accuracy | 0.60 |
| Grounded term recall | 1.00 |
| Citation coverage | 1.00 |
| Chat contract accuracy | 1.00 |
| Token local-provider | 53 |
| Chi phí model ngoài | 0 USD |

Routing thiếu `LEGAL` và `KNOWLEDGE` là khoảng trống đã biết, không phải regression
của Giai đoạn 1. Latency trong báo cáo chỉ đo component chạy trong process và không
đại diện cho network, database hoặc model production.

Chạy lại baseline:

```powershell
cd apps/ai-service
python -m evaluation.run_baseline `
  --output evaluation/baselines/pre_langchain_v1.json
```

## Điều kiện chuyển sang Giai đoạn 2

- AI Service test và backend client contract test phải đạt 100%;
- runtime mặc định và khi adapter không khả dụng đều phải fallback về legacy;
- contract hiện tại không được thay đổi breaking;
- mọi dataset evaluation phải là dữ liệu tổng hợp hoặc đã được phê duyệt;
- trước canary theo tenant phải bổ sung authorized execution context do backend ký.
