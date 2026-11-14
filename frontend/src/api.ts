import type {
  AskRequest,
  AskResponse,
  ConversationDetail,
  ConversationSummary,
  DocumentInfo,
  IngestResponse,
} from "./types";

const BASE = "/api/v1";

// 从 localStorage 读取可选 API Key（对应后端 API_KEY 校验）
function authHeaders(): Record<string, string> {
  const key = localStorage.getItem("reviewagent.apikey") || "";
  return key ? { Authorization: `Bearer ${key}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ── 知识库 ──────────────────────────────────────────────
export async function listDocuments(): Promise<DocumentInfo[]> {
  const res = await fetch(`${BASE}/documents`, { headers: authHeaders() });
  return handle<DocumentInfo[]>(res);
}

export async function uploadDocument(
  file: File,
  meta: { audit_ctx_id?: string; doc_type?: string; audit_year?: number; department?: string },
): Promise<IngestResponse> {
  const fd = new FormData();
  fd.append("file", file);
  if (meta.audit_ctx_id) fd.append("audit_ctx_id", meta.audit_ctx_id);
  if (meta.doc_type) fd.append("doc_type", meta.doc_type);
  if (meta.audit_year) fd.append("audit_year", String(meta.audit_year));
  if (meta.department) fd.append("department", meta.department);
  const res = await fetch(`${BASE}/documents`, { method: "POST", body: fd, headers: authHeaders() });
  return handle<IngestResponse>(res);
}

export async function retryDocument(documentId: string): Promise<IngestResponse> {
  const res = await fetch(`${BASE}/documents/${documentId}/retry`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle<IngestResponse>(res);
}

export async function deleteDocument(documentId: string): Promise<void> {
  // 后端目前没有 DELETE 接口；若日后加上将自动生效。
  const res = await fetch(`${BASE}/documents/${documentId}`, { method: "DELETE", headers: authHeaders() });
  if (res.status === 404 || res.status === 405) return;
  await handle<unknown>(res);
}

// ── 问答 ──────────────────────────────────────────────
export async function ask(req: AskRequest): Promise<AskResponse> {
  const res = await fetch(`${BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(req),
  });
  return handle<AskResponse>(res);
}

export const FEEDBACK_REASONS: Record<string, string> = {
  factual_error: "事实错误",
  citation_mismatch: "引用不符",
  incomplete: "回答不完整",
  other: "其他",
};

export async function sendFeedback(
  answerId: string,
  rating: 1 | -1,
  opts?: { reason?: string; comment?: string },
): Promise<void> {
  const res = await fetch(`${BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      answer_id: answerId,
      rating,
      reason: rating === -1 ? opts?.reason ?? null : null,
      comment: rating === -1 ? opts?.comment ?? null : null,
      user_id: "web",
    }),
  });
  await handle<unknown>(res);
}

// ── 会话历史 ──────────────────────────────────────────
export async function listConversations(userId: string): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/conversations?user_id=${encodeURIComponent(userId)}`, {
    headers: authHeaders(),
  });
  return handle<ConversationSummary[]>(res);
}

export async function getConversation(conversationId: string, userId: string): Promise<ConversationDetail> {
  const res = await fetch(
    `${BASE}/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(userId)}`,
    { headers: authHeaders() },
  );
  return handle<ConversationDetail>(res);
}

export async function deleteConversation(conversationId: string, userId: string): Promise<void> {
  const res = await fetch(
    `${BASE}/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await handle<unknown>(res);
}

export async function decideMemoryProposal(
  proposalId: string,
  accept: boolean,
): Promise<{ id: string; status: string }> {
  const res = await fetch(`${BASE}/memory/proposals/${proposalId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ accept, user_id: "web" }),
  });
  return handle<{ id: string; status: string }>(res);
}

// ── 健康 ──────────────────────────────────────────────
export async function health(): Promise<{ status: string; checkpointer: string }> {
  const res = await fetch("/health", { headers: authHeaders() });
  return handle<{ status: string; checkpointer: string }>(res);
}
