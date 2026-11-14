// ── 知识库 ──────────────────────────────────────────────
export interface DocumentInfo {
  id: string;
  filename: string | null;
  status: "uploaded" | "parsing" | "indexing" | "ready" | "failed";
  parse_meta: Record<string, unknown> | null;
  error: string | null;
}

export interface IngestResponse {
  document_id: string;
  status: string;
  chunks: number;
  pages?: number;
  tables?: number;
  error?: string | null;
}

// ── 问答 ──────────────────────────────────────────────
export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  page?: number | null;
  quote: string;
}

export interface AskRequest {
  question: string;
  user_id?: string;
  thread_id?: string | null;
  conversation_id?: string | null;
  audit_ctx_id?: string | null;
  audit_context_text?: string;
}

export interface MemoryProposal {
  id: string;
  fact_type: string;
  content: string;
  importance: number;
}

export interface AskResponse {
  answer_id: string;
  status: string;
  answer_markdown: string;
  citations: Citation[];
  confidence: "high" | "medium" | "low";
  caveats: string[];
  thread_id: string;
  conversation_id: string;
  latency_ms: number | null;
  follow_up_questions: string[];
  memory_proposals: MemoryProposal[];
}

// ── 对话历史（前端本地维护）────────────────────────────
export interface Turn {
  id: string;
  question: string;
  answer: AskResponse | null;
  loading: boolean;
  error: string | null;
}

// ── 会话历史（来自后端 /conversations）──────────────────
export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string | null;
  citations: Citation[] | null;
  answer: AskResponse | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  created_at: string;
  messages: StoredMessage[];
}
