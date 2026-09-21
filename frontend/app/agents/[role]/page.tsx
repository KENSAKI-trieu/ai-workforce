"use client";

import axios from "axios";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  Bot,
  CheckCircle2,
  Circle,
  Copy,
  Download,
  History,
  LoaderCircle,
  MessageSquare,
  Plus,
  RefreshCw,
  Save,
  Send,
  Settings,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";

import Sidebar from "@/components/Sidebar";
import ChatMessageContent from "@/components/chat/ChatMessageContent";
import {
  ChatAttachment,
  HRChatTools,
  HRMessageCard,
} from "@/components/hr/HRChatTools";
import api from "@/lib/api";
import { ExecutionPhase, streamAgentChat } from "@/lib/chatStream";
import { useAuthStore } from "@/store/useAuthStore";

interface Agent {
  id: string;
  name: string;
  role_code: string;
  system_prompt: string;
  prompt_overlay: string | null;
  model_name: string;
  is_active: boolean;
  tools_access: string[];
  allowed_actions: string[];
  disallowed_actions: string[];
  knowledge_access: string[];
  avatar_emoji?: string;
  description?: string;
}

interface Citation {
  document_name?: string;
  section_title?: string;
  citation_tag?: string;
}

interface ChatResponse {
  conversation_id: string;
  message_id: string;
  reply: string;
  citations: Citation[];
  hr_card?: Record<string, unknown> | null;
}

const EXECUTION_LABELS: Record<ExecutionPhase, string> = {
  ANALYZING: "Đang phân tích yêu cầu",
  SEARCHING: "Đang tìm tài liệu",
  TOOL_CALLING: "Đang gọi công cụ",
  WAITING_APPROVAL: "Đang chờ phê duyệt",
  COMPLETED: "Đã hoàn thành",
};

const EXECUTION_PHASES = Object.keys(EXECUTION_LABELS) as ExecutionPhase[];

interface ChatMessage {
  id: string;
  sender: "USER" | "ASSISTANT";
  content: string;
  citations: Citation[];
  attachments?: ChatAttachment[];
  tools_executed?: Record<string, unknown>[];
  feedback_rating: number | null;
  created_at: string | null;
}

interface Conversation {
  id: string;
  title: string;
  agent_role: string;
  agent_name?: string;
  owner_id: string;
  is_shared: boolean;
  message_count: number;
  updated_at?: string;
}

interface ConversationDetail {
  id: string;
  title: string;
  agent_role: string;
  is_shared: boolean;
  messages: ChatMessage[];
}

interface AgentToolOption {
  name: string;
  description: string;
}

interface KnowledgeChunkOption {
  id: string;
  chunk_index: number;
  section_title: string;
  page_start: number | null;
  page_end: number | null;
  status: string;
  confidentiality: string;
}

interface KnowledgeDocumentOption {
  document_id: string;
  document_name: string;
  document_title: string;
  collection_name: string;
  department_access: string;
  confidentiality: string;
  status: string;
  chunks: KnowledgeChunkOption[];
}

interface AgentConfigurationOptions {
  agent_role: string;
  tools: AgentToolOption[];
  documents: KnowledgeDocumentOption[];
}

function messageFrom(error: unknown) {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : "Không thể xử lý yêu cầu.";
  }
  const detail = error.response?.data?.detail;
  return typeof detail === "string" ? detail : error.message;
}

export default function AgentPage() {
  const params = useParams<{ role: string }>();
  const role = String(params.role || "").toUpperCase();
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [conversationLoading, setConversationLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [streamedReply, setStreamedReply] = useState("");
  const [streamPhase, setStreamPhase] = useState<ExecutionPhase>("ANALYZING");
  const [streamPhases, setStreamPhases] = useState<ExecutionPhase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [deniedTools, setDeniedTools] = useState<string[]>([]);
  const [knowledgeAccess, setKnowledgeAccess] = useState<string[]>([]);
  const [configurationOptions, setConfigurationOptions] = useState<AgentConfigurationOptions | null>(null);
  const [configurationLoading, setConfigurationLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const fetchConversations = useCallback(async () => {
    const { data } = await api.get<Conversation[]>("/api/v1/agent/conversations");
    const filtered = data.filter((item) => item.agent_role === role);
    setConversations(filtered);
  }, [role]);

  const fetchPageData = useCallback(async () => {
    setError(null);
    try {
      const [agentResponse, conversationsResponse] = await Promise.all([
        api.get<Agent>(`/api/v1/agents/${role}`),
        api.get<Conversation[]>("/api/v1/agent/conversations"),
      ]);
      const value = agentResponse.data;
      const filtered = conversationsResponse.data.filter(
        (item) => item.agent_role === role,
      );
      setAgent(value);
      setConversations(filtered);
      const initialConversationId = filtered[0]?.id || null;
      setConversationId(initialConversationId);
      if (initialConversationId) {
        const { data } = await api.get<ConversationDetail>(
          `/api/v1/agent/conversations/${initialConversationId}`,
        );
        setMessages(data.messages);
      } else {
        setMessages([]);
      }
      setPrompt(value.prompt_overlay ?? "");
      setDeniedTools(value.disallowed_actions);
      setTools(value.tools_access.filter((tool) => !value.disallowed_actions.includes(tool)));
      setKnowledgeAccess(value.knowledge_access.length ? value.knowledge_access : ["*"]);
    } catch (reason) {
      setError(messageFrom(reason));
    }
  }, [role]);

  const loadConversation = useCallback(async (id: string) => {
    setConversationLoading(true);
    setError(null);
    try {
      const { data } = await api.get<ConversationDetail>(
        `/api/v1/agent/conversations/${id}`,
      );
      setMessages(data.messages);
    } catch (reason) {
      setError(messageFrom(reason));
      setMessages([]);
    } finally {
      setConversationLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => void fetchPageData(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchPageData, hasHydrated, isAuthenticated, router]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, streamedReply, streamPhase]);

  const submitMessage = async (content: string) => {
    if (!content || !agent || busy) return;
    const optimisticMessage: ChatMessage = {
      id: `pending-${Date.now()}`,
      sender: "USER",
      content,
      citations: [],
      feedback_rating: null,
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimisticMessage]);
    setMessage("");
    setBusy(true);
    setStreamedReply("");
    setStreamPhase("ANALYZING");
    setStreamPhases(["ANALYZING"]);
    setError(null);
    try {
      let completedResponse: ChatResponse | null = null;
      await streamAgentChat({
        agent_role: agent.role_code,
        message: content,
        conversation_id: conversationId,
      }, (event) => {
        if (event.event === "status") {
          setStreamPhase(event.phase);
          setStreamPhases((current) => current.includes(event.phase)
            ? current
            : [...current, event.phase]);
        } else if (event.event === "token") {
          setStreamedReply((current) => current + event.delta);
        } else if (event.event === "complete") {
          completedResponse = event as unknown as ChatResponse;
        } else if (event.event === "error") {
          throw new Error(event.message);
        }
      });
      if (!completedResponse) throw new Error("Luồng phản hồi kết thúc chưa hoàn chỉnh.");
      const data = completedResponse as ChatResponse;
      setConversationId(data.conversation_id);
      await Promise.all([
        loadConversation(data.conversation_id),
        fetchConversations(),
      ]);
    } catch (reason) {
      setMessages((current) => current.filter((item) => item.id !== optimisticMessage.id));
      setMessage(content);
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
      setStreamedReply("");
      setStreamPhases([]);
      composerRef.current?.focus();
    }
  };

  const sendMessage = (event: FormEvent) => {
    event.preventDefault();
    void submitMessage(message.trim());
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  const startNewConversation = () => {
    setConversationId(null);
    setMessages([]);
    setError(null);
    window.setTimeout(() => composerRef.current?.focus(), 0);
  };

  const selectConversation = async (id: string) => {
    if (id === conversationId) return;
    setConversationId(id);
    await loadConversation(id);
  };

  const regenerate = async () => {
    if (!conversationId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<ChatResponse>(
        `/api/v1/agent/conversations/${conversationId}/regenerate`,
      );
      await loadConversation(conversationId);
      await fetchConversations();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  const rate = async (messageId: string, rating: -1 | 1) => {
    try {
      await api.post(`/api/v1/agent/messages/${messageId}/feedback`, { rating });
      setMessages((current) => current.map((item) => (
        item.id === messageId ? { ...item, feedback_rating: rating } : item
      )));
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const createTask = async () => {
    if (!conversationId) return;
    try {
      await api.post(`/api/v1/agent/conversations/${conversationId}/task`, {
        title: `Theo dõi hội thoại với ${agent?.name || role}`,
      });
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const exportConversation = async () => {
    if (!conversationId) return;
    try {
      const { data } = await api.get<string>(
        `/api/v1/agent/conversations/${conversationId}/export`,
        { responseType: "text" },
      );
      const url = URL.createObjectURL(new Blob([data], { type: "text/markdown" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `conversation-${conversationId}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const saveConfiguration = async () => {
    if (!agent) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.patch<Agent>(`/api/v1/agents/${role}`, {
        prompt_overlay: prompt,
        tools_access: tools,
        allowed_actions: tools,
        disallowed_actions: deniedTools.filter((tool) => !tools.includes(tool)),
        knowledge_access: knowledgeAccess.length ? knowledgeAccess : ["none"],
      });
      setAgent(data);
      setShowSettings(false);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  const openSettings = async () => {
    setShowSettings(true);
    setConfigurationLoading(true);
    setError(null);
    try {
      const { data } = await api.get<AgentConfigurationOptions>(
        `/api/v1/agents/${role}/configuration-options`,
      );
      setConfigurationOptions(data);
    } catch (reason) {
      setError(messageFrom(reason));
      setShowSettings(false);
    } finally {
      setConfigurationLoading(false);
    }
  };

  const toggleTool = (toolName: string) => {
    setDeniedTools((current) => current.filter((item) => item !== toolName));
    setTools((current) => current.includes(toolName)
      ? current.filter((item) => item !== toolName)
      : [...current, toolName].sort());
  };

  const toggleKnowledge = (selector: string) => {
    setKnowledgeAccess((current) => {
      const base = current.filter((item) => item !== "*" && item !== "none");
      return base.includes(selector)
        ? base.filter((item) => item !== selector)
        : [...base, selector].sort();
    });
  };

  const selectAllKnowledge = () => setKnowledgeAccess(["*"]);
  const selectNoKnowledge = () => setKnowledgeAccess(["none"]);

  if (!hasHydrated || !isAuthenticated) return null;
  const canConfigure = ["Owner", "Admin", "CEO"].includes(user?.role || "");
  const canManageHR = ["Owner", "CEO"].includes(user?.role || "") || (
    user?.department === "HR" && ["Manager", "Admin"].includes(user?.role || "")
  );
  const canApproveHR = canManageHR || ["Manager", "Admin"].includes(user?.role || "");
  const canSearchEmployees = ["Owner", "CEO", "Admin", "Manager"].includes(user?.role || "");
  const selectedConversation = conversations.find((item) => item.id === conversationId);
  const isReadOnly = Boolean(
    selectedConversation && selectedConversation.owner_id !== user?.id,
  );

  return (
    <div className="ai-chat-page">
      <Sidebar agentStatuses={agent ? { [role]: agent.is_active } : {}} />
      <div className="ai-chat-shell">
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>AI Employees</span>
            <span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">{agent?.name || role}</span>
          </div>
          <span className={`ta-badge ${agent?.is_active ? "ta-badge-success" : "ta-badge-danger"}`}>
            {agent?.is_active ? "Đang hoạt động" : "Đã tắt"}
          </span>
        </header>

        <main className="ai-chat-workspace">
          <aside className="ai-chat-history">
            <div className="ai-chat-history-header">
              <div>
                <span className="ai-chat-eyebrow">Tin nhắn</span>
                <h2><History size={17} /> Hội thoại</h2>
              </div>
              <button
                type="button"
                className="ai-chat-icon-button"
                onClick={startNewConversation}
                aria-label="Tạo hội thoại mới"
                title="Hội thoại mới"
              >
                <Plus size={18} />
              </button>
            </div>
            <button
              type="button"
              className="ai-chat-new-button"
              onClick={startNewConversation}
            >
              <MessageSquare size={17} />
              Hội thoại mới
            </button>
            <div className="ai-chat-history-list">
              {conversations.length === 0 && (
                <p className="ai-chat-history-empty">Các cuộc trò chuyện sẽ xuất hiện ở đây.</p>
              )}
              {conversations.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`ai-chat-history-item ${conversationId === item.id ? "active" : ""}`}
                  onClick={() => void selectConversation(item.id)}
                >
                  <MessageSquare size={15} />
                  <span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.message_count} tin nhắn
                      {item.is_shared ? " · Được chia sẻ" : ""}
                    </small>
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <section className="ai-chat-panel">
            <header className="ai-chat-header">
              <div className="ai-chat-agent">
                <div className="ai-chat-agent-avatar">
                  {agent?.avatar_emoji || <Bot size={22} />}
                  <span className={agent?.is_active ? "online" : ""} />
                </div>
                <div>
                  <h1>{agent?.name || role}</h1>
                  <p>
                    {isReadOnly
                      ? "Hội thoại được chia sẻ · Chỉ đọc"
                      : agent?.is_active
                        ? "Đang trực tuyến"
                        : "Hiện không hoạt động"}
                  </p>
                </div>
              </div>
              <div className="ai-chat-header-actions">
                {conversationId && (
                  <button type="button" onClick={() => void exportConversation()}>
                    <Download size={16} /> Export
                  </button>
                )}
                {canConfigure && (
                  <button type="button" onClick={() => void openSettings()}>
                    <Settings size={16} /> Cấu hình
                  </button>
                )}
              </div>
            </header>

            <div className="ai-chat-messages" aria-live="polite">
              {conversationLoading && (
                <div className="ai-chat-loading">Đang tải hội thoại…</div>
              )}
              {!conversationLoading && messages.length === 0 && (
                <div className="ai-chat-welcome">
                  <div className="ai-chat-welcome-avatar">
                    {agent?.avatar_emoji || <Bot size={30} />}
                  </div>
                  <h2>Chào bạn, tôi là {agent?.name || role}</h2>
                  <p>{agent?.description || "Tôi có thể hỗ trợ bạn xử lý công việc và tra cứu thông tin."}</p>
                  <span>Hãy nhập câu hỏi để bắt đầu cuộc trò chuyện.</span>
                </div>
              )}
              {!conversationLoading && messages.map((item) => (
                <article
                  className={`ai-chat-message ${item.sender === "USER" ? "user" : "assistant"}`}
                  key={item.id}
                >
                  {item.sender === "ASSISTANT" && (
                    <div className="ai-chat-message-avatar">
                      {agent?.avatar_emoji || <Bot size={17} />}
                    </div>
                  )}
                  <div className="ai-chat-message-body">
                    <div className="ai-chat-bubble">
                      {item.sender === "ASSISTANT"
                        ? <ChatMessageContent content={item.content} />
                        : item.content}
                    </div>
                    {item.citations.length > 0 && (
                      <div className="ai-chat-citations">
                        <strong>Nguồn tham khảo</strong>
                        {item.citations.map((citation, index) => (
                          <span key={`${item.id}-citation-${index}`}>
                            {citation.citation_tag || citation.document_name || "Tài liệu nội bộ"}
                            {citation.section_title ? ` · ${citation.section_title}` : ""}
                          </span>
                        ))}
                      </div>
                    )}
                    {item.attachments?.map((attachment, index) => (
                      <HRMessageCard
                        key={`${item.id}-attachment-${index}`}
                        attachment={attachment}
                      />
                    ))}
                    {item.sender === "ASSISTANT" && (
                      <div className="ai-chat-message-actions">
                        <button
                          type="button"
                          onClick={() => void navigator.clipboard.writeText(item.content)}
                          title="Sao chép"
                        >
                          <Copy size={14} /> Copy
                        </button>
                        {!isReadOnly && (
                          <>
                            <button
                              type="button"
                              className={item.feedback_rating === 1 ? "selected" : ""}
                              onClick={() => void rate(item.id, 1)}
                              title="Câu trả lời hữu ích"
                            >
                              <ThumbsUp size={14} />
                            </button>
                            <button
                              type="button"
                              className={item.feedback_rating === -1 ? "selected" : ""}
                              onClick={() => void rate(item.id, -1)}
                              title="Câu trả lời chưa tốt"
                            >
                              <ThumbsDown size={14} />
                            </button>
                          </>
                        )}
                        {!isReadOnly && item.id === messages.at(-1)?.id && (
                          <>
                            <button
                              type="button"
                              onClick={() => void regenerate()}
                              disabled={busy}
                            >
                              <RefreshCw size={14} /> Tạo lại
                            </button>
                            <button type="button" onClick={() => void createTask()}>
                              Tạo task
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </article>
              ))}
              {busy && (
                <div className="ai-chat-message assistant">
                  <div className="ai-chat-message-avatar">
                    {agent?.avatar_emoji || <Bot size={17} />}
                  </div>
                  <div className="ai-chat-streaming" aria-live="polite">
                    <div className="ai-chat-execution-status" aria-label={EXECUTION_LABELS[streamPhase]}>
                      {EXECUTION_PHASES.map((phase) => {
                        const reached = streamPhases.includes(phase);
                        const active = phase === streamPhase && phase !== "COMPLETED";
                        return (
                          <div
                            className={`ai-chat-execution-step ${active ? "active" : ""} ${reached ? "reached" : ""}`}
                            key={phase}
                          >
                            {active
                              ? <LoaderCircle size={14} className="ai-chat-spin" />
                              : reached
                                ? <CheckCircle2 size={14} />
                                : <Circle size={14} />}
                            <span>{EXECUTION_LABELS[phase]}</span>
                          </div>
                        );
                      })}
                    </div>
                    {streamedReply ? (
                      <div className="ai-chat-bubble">
                        <ChatMessageContent content={streamedReply} />
                      </div>
                    ) : (
                      <div className="ai-chat-typing" aria-hidden="true">
                        <span /><span /><span />
                      </div>
                    )}
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            <div className="ai-chat-composer-wrap">
              {role === "HR" && !isReadOnly && (
                <HRChatTools
                  disabled={busy || !agent?.is_active}
                  canManageHR={canManageHR}
                  canApprove={canApproveHR}
                  canSearchEmployees={canSearchEmployees}
                  onPrompt={submitMessage}
                />
              )}
              {error && (
                <div className="ai-chat-error">
                  {error}
                  <button type="button" onClick={() => setError(null)} aria-label="Đóng">
                    <X size={14} />
                  </button>
                </div>
              )}
              <form className="ai-chat-composer" onSubmit={sendMessage}>
                <textarea
                  ref={composerRef}
                  rows={1}
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder={isReadOnly ? "Hội thoại này đang ở chế độ chỉ đọc" : `Nhắn tin cho ${agent?.name || role}…`}
                  disabled={busy || !agent?.is_active || isReadOnly}
                  aria-label="Nội dung tin nhắn"
                />
                <button
                  type="submit"
                  disabled={busy || !message.trim() || !agent?.is_active || isReadOnly}
                  aria-label="Gửi tin nhắn"
                  title="Gửi"
                >
                  <Send size={19} />
                </button>
              </form>
              <p>Enter để gửi · Shift + Enter để xuống dòng</p>
            </div>
          </section>
        </main>
      </div>

      {showSettings && canConfigure && agent && (
        <div className="ai-chat-settings-backdrop" onMouseDown={() => setShowSettings(false)}>
          <section
            className="ai-chat-settings"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`Cấu hình ${agent.name}`}
          >
            <header>
              <div>
                <span className="ai-chat-eyebrow">AI Employee</span>
                <h2>Cấu hình {agent.name}</h2>
              </div>
              <button type="button" onClick={() => setShowSettings(false)} aria-label="Đóng">
                <X size={20} />
              </button>
            </header>
            <div className="ai-chat-settings-content">
              <label>
                Quy ước riêng của công ty
                <textarea
                  rows={7}
                  value={prompt}
                  placeholder="Ví dụ: xưng hô với người dùng là anh/chị; luôn nhắc liên hệ phòng Nhân sự khi nói về lương."
                  onChange={(event) => setPrompt(event.target.value)}
                />
                <small>
                  Phần này được nối thêm vào cuối prompt trả lời mặc định, chỉ ảnh hưởng tới
                  cách diễn đạt câu trả lời. Để trống thì agent chạy đúng prompt gốc. Muốn
                  can thiệp vào định tuyến ý định hoặc bóc tham số thì dùng trang Plugin.
                </small>
              </label>
              <section className="ai-agent-config-section">
                <div className="ai-agent-config-heading">
                  <div><strong>Tools được phép</strong><span>AI chỉ có thể gọi những tool được chọn.</span></div>
                  <small>{tools.length} tool</small>
                </div>
                <div className="ai-agent-tool-grid">
                  {configurationLoading && <span className="ai-agent-config-empty">Đang tải danh sách tool…</span>}
                  {configurationOptions?.tools.map((tool) => (
                    <label className="ai-agent-check-card" key={tool.name}>
                      <input type="checkbox" checked={tools.includes(tool.name)} onChange={() => toggleTool(tool.name)} />
                      <span><strong>{tool.name}</strong><small>{tool.description}</small></span>
                    </label>
                  ))}
                </div>
              </section>

              <section className="ai-agent-config-section">
                <div className="ai-agent-config-heading">
                  <div><strong>Tài liệu và chunks được phép</strong><span>ACL của AI luôn được giao với quyền tài liệu của người đang chat.</span></div>
                  <div className="ai-agent-config-presets">
                    <button type="button" className={knowledgeAccess.includes("*") ? "active" : ""} onClick={selectAllKnowledge}>Tất cả</button>
                    <button type="button" className={knowledgeAccess.includes("none") ? "active" : ""} onClick={selectNoKnowledge}>Không tài liệu</button>
                  </div>
                </div>
                <div className="ai-agent-document-list">
                  {configurationOptions?.documents.length === 0 && <span className="ai-agent-config-empty">Kho tri thức chưa có tài liệu.</span>}
                  {configurationOptions?.documents.map((document) => {
                    const documentSelector = `document:${document.document_id}`;
                    const documentSelected = knowledgeAccess.includes("*") || knowledgeAccess.includes(documentSelector);
                    return (
                      <article className="ai-agent-document-option" key={document.document_id}>
                        <label>
                          <input type="checkbox" checked={documentSelected} disabled={knowledgeAccess.includes("*")} onChange={() => toggleKnowledge(documentSelector)} />
                          <span><strong>{document.document_title}</strong><small>{document.collection_name} · {document.department_access} · {document.confidentiality} · {document.chunks.length} chunks</small></span>
                        </label>
                        <div className="ai-agent-chunk-list">
                          {document.chunks.map((chunk) => {
                            const chunkSelector = `chunk:${chunk.id}`;
                            return (
                              <label key={chunk.id}>
                                <input
                                  type="checkbox"
                                  checked={documentSelected || knowledgeAccess.includes(chunkSelector)}
                                  disabled={documentSelected}
                                  onChange={() => toggleKnowledge(chunkSelector)}
                                />
                                <span>{chunk.section_title}<small>Chunk {chunk.chunk_index}{chunk.page_start ? ` · trang ${chunk.page_start}` : ""} · {chunk.confidentiality}</small></span>
                              </label>
                            );
                          })}
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            </div>
            <footer>
              <button type="button" className="secondary" onClick={() => setShowSettings(false)}>
                Hủy
              </button>
              <button type="button" className="primary" disabled={busy || configurationLoading} onClick={() => void saveConfiguration()}>
                <Save size={16} /> Lưu cấu hình
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
