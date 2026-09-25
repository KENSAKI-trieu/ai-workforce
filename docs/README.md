# AI WORKFORCE - TECHNICAL DOCUMENTATION (SDD)

## User Manual / Hướng dẫn sử dụng

- [日本語ユーザーマニュアル](manuals/USER_MANUAL_JA.md)
- [Hướng dẫn sử dụng tiếng Việt](manuals/USER_MANUAL_VI.md)

Chào mừng bạn đến với bộ tài liệu kỹ thuật chi tiết **Software Design Document (SDD)** cho hệ thống **AI Workforce - Enterprise Multi-Agent Platform**.

## BỘ CHỈ MỤC TÀI LIỆU (TABLE OF CONTENTS)

| STT | Tài liệu | Mô tả |
| :---: | :--- | :--- |
| **01** | [01-overview.md](01-overview.md) | Tổng quan sản phẩm, sứ mệnh & triết lý AI Employees |
| **02** | [02-requirements.md](02-requirements.md) | Phân tích Yêu cầu Chức năng (FR) & Phi Chức năng (NFR) |
| **03** | [03-system-architecture.md](03-system-architecture.md) | Kiến trúc hệ thống chi tiết & Sơ đồ tương tác |
| **04** | [04-database-design.md](04-database-design.md) | Thiết kế Cơ sở Dữ liệu SQL (PostgreSQL) & Vector Embeddings |
| **05** | [05-agent-design.md](05-agent-design.md) | Thiết kế Chi tiết 7 AI Agents (Prompts, States, Tools) |
| **06** | [06-rag-system.md](06-rag-system.md) | Kiến trúc Hybrid RAG (BGE-M3 + BM25 + Reranker) |
| **07** | [07-tool-calling.md](07-tool-calling.md) | Chuẩn Giao Tiếp Tool Calling & MCP (Model Context Protocol) |
| **08** | [08-workflows.md](08-workflows.md) | Luồng Quy Trình Nghiệp Vụ & Duyệt Duyệt Tương Tác Người (HITL) |
| **09** | [09-api-design.md](09-api-design.md) | Đặc tả RESTful API & WebSocket Real-time Stream Protocol |
| **10** | [10-frontend.md](10-frontend.md) | Giao diện Notion + Slack + Jira Hybrid UX/UI Design |
| **11** | [11-deployment.md](11-deployment.md) | Hướng dẫn Đóng gói Docker, CI/CD & Triển khai Hạ tầng |
| **12** | [12-roadmap.md](12-roadmap.md) | Lộ trình Phát triển 10 tuần (Sprint 1 đến Sprint 5) |
| **13** | [13-future-features.md](13-future-features.md) | Mở rộng Multi-tenant SaaS, Integrations & Agent Benchmarks |


## Kiến trúc AI và LangChain/LangGraph

| Tài liệu | Mô tả |
| :--- | :--- |
| [AI_SERVICE_ARCHITECTURE.md](AI_SERVICE_ARCHITECTURE.md) | Kiến trúc AI service |
| [14-embedding-pipeline.md](14-embedding-pipeline.md) | Pipeline embedding |
| [15-langchain/](15-langchain) · [16](16-langchain-phase-1-baseline.md) · [17](17-langchain-tool-registry-phase-3.md) · [18](18-langchain-middleware-phase-4.md) | Các giai đoạn chuyển sang LangChain |
| [19-langgraph-orchestration-phase-5.md](19-langgraph-orchestration-phase-5.md) · [20](20-langgraph-persistence-hitl-phase-6.md) · [21](21-streaming-ui-phase-7.md) | LangGraph: orchestration, checkpoint/HITL, streaming |
| [AI_HR_V1.md](AI_HR_V1.md) | Thiết kế HR agent v1 |

## Kế hoạch, báo cáo, hướng dẫn

- [plans/](plans) — kế hoạch có ghi ngày; mới nhất: [REFACTOR_PLAN_2026-09-24.md](plans/REFACTOR_PLAN_2026-09-24.md) (cấu trúc backend/ai-service hiện tại).
- [reports/](reports) — báo cáo thay đổi và rà soát có ghi ngày; mô tả code tại thời điểm viết.
- [manuals/](manuals) — hướng dẫn sử dụng tiếng Nhật và tiếng Việt.

---
*Vui lòng xem file [BLUEPRINT.md](../BLUEPRINT.md) ở thư mục gốc để có cái nhìn tổng quan toàn diện.*
