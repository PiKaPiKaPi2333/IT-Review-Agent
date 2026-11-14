import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ask,
  decideMemoryProposal,
  deleteConversation,
  FEEDBACK_REASONS,
  getConversation,
  listConversations,
  sendFeedback,
} from "../api";
import type { AskResponse, ConversationDetail, ConversationSummary, StoredMessage, Turn } from "../types";

// 当前前端只有一个用户身份，与问答、反馈接口保持一致
const USER_ID = "web";

const STATUS_LABELS: Record<string, string> = {
  ok: "已核验",
  degraded: "降级（引用未完全核验）",
  insufficient_evidence: "证据不足",
  needs_clarification: "需澄清",
  out_of_scope: "超范围",
  error: "错误",
  archived: "历史记录（旧版本未保存完整回答）",
};

function genId() {
  return Math.random().toString(36).slice(2, 10);
}

// 旧数据没有保存完整回答时，用正文和引用拼一个最小可展示的结构
function legacyAnswer(m: StoredMessage): AskResponse {
  return {
    answer_id: m.id,
    status: "archived",
    answer_markdown: m.content ?? "",
    citations: m.citations ?? [],
    confidence: "low",
    caveats: [],
    thread_id: "",
    conversation_id: "",
    latency_ms: null,
    follow_up_questions: [],
    memory_proposals: [],
  };
}

// 把后端保存的回答补全为完整结构：保存时排除了 memory_proposals，旧数据也可能缺字段
function normalizeAnswer(raw: Partial<AskResponse>, fallbackId: string): AskResponse {
  return {
    answer_id: raw.answer_id ?? fallbackId,
    status: raw.status ?? "ok",
    answer_markdown: raw.answer_markdown ?? "",
    citations: raw.citations ?? [],
    confidence: raw.confidence ?? "low",
    caveats: raw.caveats ?? [],
    thread_id: raw.thread_id ?? "",
    conversation_id: raw.conversation_id ?? "",
    latency_ms: raw.latency_ms ?? null,
    follow_up_questions: raw.follow_up_questions ?? [],
    memory_proposals: raw.memory_proposals ?? [],
  };
}

// 把后端的消息序列还原成前端的问答轮次
function turnsFromDetail(detail: ConversationDetail): Turn[] {
  const turns: Turn[] = [];
  for (const m of detail.messages) {
    if (m.role === "user") {
      turns.push({ id: m.id, question: m.content ?? "", answer: null, loading: false, error: null });
    } else if (turns.length > 0) {
      const answer = m.answer ? normalizeAnswer(m.answer, m.id) : legacyAnswer(m);
      turns[turns.length - 1].answer = answer;
    }
  }
  return turns;
}

function formatTime(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { hour12: false });
}

// ── Single answer block ─────────────────────────────────────
function AnswerView({ answer }: { answer: AskResponse }) {
  const [feedback, setFeedback] = useState<1 | -1 | null>(null);
  const [fbBusy, setFbBusy] = useState(false);
  // 👎 后收集原因与评论
  const [showDownForm, setShowDownForm] = useState(false);
  const [downReason, setDownReason] = useState("factual_error");
  const [downComment, setDownComment] = useState("");
  // 长期记忆候选：只有用户点"记住"才会写入
  const [memoryState, setMemoryState] = useState<Record<string, "accepted" | "rejected">>({});
  const [memoryBusy, setMemoryBusy] = useState<string | null>(null);
  const [memoryError, setMemoryError] = useState<string | null>(null);

  async function decideMemory(proposalId: string, accept: boolean) {
    setMemoryBusy(proposalId);
    setMemoryError(null);
    try {
      const res = await decideMemoryProposal(proposalId, accept);
      setMemoryState((s) => ({ ...s, [proposalId]: res.status === "accepted" ? "accepted" : "rejected" }));
    } catch (e) {
      setMemoryError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setMemoryBusy(null);
    }
  }

  async function vote(rating: 1 | -1, opts?: { reason?: string; comment?: string }) {
    setFbBusy(true);
    try {
      await sendFeedback(answer.answer_id, rating, opts);
      setFeedback(rating);
      setShowDownForm(false);
    } catch {
      /* 静默：反馈失败不阻塞阅读 */
    } finally {
      setFbBusy(false);
    }
  }

  return (
    <div className="answer-card">
      {/* Meta row */}
      <div className="answer-meta">
        <span className={`badge badge-${answer.status === "ok" ? "ready" : answer.status === "error" ? "failed" : "parsing"}`}>
          {STATUS_LABELS[answer.status] ?? answer.status}
        </span>
        <span>置信度: <strong>{answer.confidence}</strong></span>
        {answer.latency_ms != null && <span>耗时 {answer.latency_ms} ms</span>}
        <span>answer_id: <code style={{ fontSize: 11 }}>{answer.answer_id.slice(0, 12)}…</code></span>
      </div>

      {/* Body */}
      {answer.answer_markdown ? (
        <div className="answer-body">
          <ReactMarkdown>{answer.answer_markdown}</ReactMarkdown>
        </div>
      ) : (
        <div className="answer-body text-muted">（无正文）</div>
      )}

      {/* Citations */}
      {answer.citations.length > 0 && (
        <div className="citations">
          <div className="citations-title">引用（{answer.citations.length}）</div>
          {answer.citations.map((c) => (
            <div key={c.index} className="citation-item">
              <span className="citation-index">[{c.index}]</span>
              <span>
                <span className="citation-doc">
                  {c.document_id.slice(0, 16)}…{c.page ? ` · 第 ${c.page} 页` : ""}
                </span>
                <span className="citation-quote">“{c.quote}”</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Caveats */}
      {answer.caveats.length > 0 && (
        <div className="caveats">
          <div className="caveats-title">注意事项</div>
          {answer.caveats.map((c, i) => (
            <div key={i} className="caveat-item">• {c}</div>
          ))}
        </div>
      )}

      {/* Feedback */}
      <div className="feedback-row">
        <span className="feedback-label">这个答案有帮助吗？</span>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => vote(1)}
          disabled={fbBusy || feedback !== null}
          title="有帮助"
          style={feedback === 1 ? { color: "var(--color-success)" } : {}}
        >
          👍
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setShowDownForm((v) => !v)}
          disabled={fbBusy || feedback !== null}
          title="需改进"
          style={feedback === -1 ? { color: "var(--color-danger)" } : {}}
        >
          👎
        </button>
        {feedback && <span className="text-muted text-sm">已记录</span>}
      </div>

      {showDownForm && feedback === null && (
        <div className="feedback-row" style={{ flexWrap: "wrap", gap: "var(--space-2)" }}>
          <select className="input" value={downReason} onChange={(e) => setDownReason(e.target.value)}>
            {Object.entries(FEEDBACK_REASONS).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <input
            className="input"
            placeholder="补充说明（可选）"
            value={downComment}
            onChange={(e) => setDownComment(e.target.value)}
            style={{ flex: 1, minWidth: 200 }}
          />
          <button
            className="btn btn-primary btn-sm"
            disabled={fbBusy}
            onClick={() => vote(-1, { reason: downReason, comment: downComment.trim() || undefined })}
          >
            提交
          </button>
        </div>
      )}

      {/* Long-term memory proposals: ask before saving */}
      {answer.memory_proposals.length > 0 && (
        <div className="memory-proposals">
          <div className="feedback-label">是否需要记住以下内容？（确认后才会保存为长期记忆）</div>
          {answer.memory_proposals.map((p) => {
            const decided = memoryState[p.id];
            return (
              <div key={p.id} className="feedback-row">
                <span className="citation-quote">{p.content}</span>
                {decided ? (
                  <span className="text-muted text-sm">{decided === "accepted" ? "已记住" : "未记住"}</span>
                ) : (
                  <>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => decideMemory(p.id, true)}
                      disabled={memoryBusy === p.id}
                    >
                      记住
                    </button>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => decideMemory(p.id, false)}
                      disabled={memoryBusy === p.id}
                    >
                      不记住
                    </button>
                  </>
                )}
              </div>
            );
          })}
          {memoryError && <div className="text-sm" style={{ color: "var(--color-danger)" }}>{memoryError}</div>}
        </div>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────
export default function QAPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [auditCtxId, setAuditCtxId] = useState("");
  const [contextText, setContextText] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [busy, setBusy] = useState(false);
  const historyRef = useRef<HTMLDivElement>(null);

  async function refreshList() {
    try {
      setConversations(await listConversations(USER_ID));
      setListError(null);
    } catch (e) {
      setListError(e instanceof Error ? e.message : "会话列表加载失败");
    }
  }

  // 首次进入：加载会话列表
  useEffect(() => {
    refreshList();
  }, []);

  // Auto-scroll to bottom on new turn
  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  async function submit() {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    const turnId = genId();
    setTurns((prev) => [...prev, { id: turnId, question: q, answer: null, loading: true, error: null }]);
    setQuestion("");
    try {
      const res = await ask({
        question: q,
        user_id: USER_ID,
        thread_id: threadId,
        conversation_id: conversationId,
        audit_ctx_id: auditCtxId || undefined,
        audit_context_text: contextText,
      });
      setTurns((prev) =>
        prev.map((t) => (t.id === turnId ? { ...t, answer: res, loading: false } : t)),
      );
      if (res.thread_id) setThreadId(res.thread_id);
      // 首轮由后端创建会话，之后的提问都要带上这个 conversation_id，历史才能串起来
      if (res.conversation_id) setConversationId(res.conversation_id);
      refreshList();
    } catch (e) {
      setTurns((prev) =>
        prev.map((t) => (t.id === turnId ? { ...t, loading: false, error: String(e) } : t)),
      );
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function newConversation() {
    setTurns([]);
    setThreadId(null);
    setConversationId(null);
    setQuestion("");
  }

  async function openConversation(id: string) {
    if (busy) return;
    try {
      const detail = await getConversation(id, USER_ID);
      setTurns(turnsFromDetail(detail));
      setThreadId(null);
      setConversationId(detail.id);
      setQuestion("");
    } catch (e) {
      setListError(e instanceof Error ? e.message : "会话加载失败");
    }
  }

  async function removeConversation(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm("确定删除这段会话？删除后不可恢复。")) return;
    try {
      await deleteConversation(id, USER_ID);
      if (id === conversationId) newConversation();
      refreshList();
    } catch (err) {
      setListError(err instanceof Error ? err.message : "删除失败");
    }
  }

  function pickFollowUp(q: string) {
    setQuestion(q);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", height: "calc(100dvh - 52px - 2 * var(--space-6))" }}>
      <div className="flex items-center justify-between">
        <h1 className="page-title" style={{ margin: 0 }}>审计问答</h1>
        <button className="btn btn-ghost btn-sm" onClick={newConversation} disabled={busy && turns.length === 0}>
          ✚ 新会话
        </button>
      </div>

      <div style={{ display: "flex", gap: "var(--space-4)", flex: 1, minHeight: 0 }}>
        {/* Conversation list */}
        <aside
          style={{
            width: 240,
            flexShrink: 0,
            overflowY: "auto",
            borderRight: "1px solid var(--color-border, #e5e5e5)",
            paddingRight: "var(--space-3)",
          }}
        >
          <div className="text-muted text-sm" style={{ marginBottom: "var(--space-2)" }}>历史会话</div>
          {listError && (
            <div className="text-sm" style={{ color: "var(--color-danger)" }}>{listError}</div>
          )}
          {conversations.length === 0 && !listError && (
            <div className="text-muted text-sm">暂无历史会话</div>
          )}
          {conversations.map((c) => (
            <div
              key={c.id}
              onClick={() => openConversation(c.id)}
              style={{
                padding: "var(--space-2)",
                borderRadius: 6,
                cursor: "pointer",
                marginBottom: 4,
                background: c.id === conversationId ? "var(--color-surface-2, #f1f5f9)" : "transparent",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <span
                  title={c.title ?? ""}
                  style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {c.title || "（无标题）"}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  title="删除会话"
                  onClick={(e) => removeConversation(c.id, e)}
                >
                  🗑
                </button>
              </div>
              <div className="text-muted text-sm">{formatTime(c.updated_at)}</div>
            </div>
          ))}
        </aside>

      <div className="qa-layout" style={{ marginTop: 0, flex: 1, minWidth: 0 }}>
        {/* History */}
        <div className="chat-history" ref={historyRef}>
          {turns.length === 0 && (
            <div className="empty-state" style={{ marginTop: "var(--space-8)" }}>
              <div style={{ fontSize: 40, marginBottom: "var(--space-3)" }}>💬</div>
              <div>向审计知识库提问，回答会附带可溯源的引用与核验结论。</div>
              <div className="text-sm" style={{ marginTop: "var(--space-2)" }}>
                支持多轮追问，系统会保留会话上下文。
              </div>
            </div>
          )}

          {turns.map((t) => (
            <div key={t.id} className="chat-turn">
              <div className="chat-question">
                <div className="chat-question-bubble">{t.question}</div>
              </div>
              <div className="chat-answer">
                {t.loading && (
                  <div className="answer-card">
                    <div className="answer-meta">
                      <span className="spinner" />
                      <span className="text-muted">正在检索知识库并生成回答…</span>
                    </div>
                    <div className="answer-body">
                      <div className="skeleton" style={{ width: "90%", marginBottom: "var(--space-2)" }} />
                      <div className="skeleton" style={{ width: "75%", marginBottom: "var(--space-2)" }} />
                      <div className="skeleton" style={{ width: "82%" }} />
                    </div>
                  </div>
                )}
                {t.error && (
                  <div className="alert alert-error">
                    请求失败：{t.error}
                  </div>
                )}
                {t.answer && (
                  <>
                    <AnswerView answer={t.answer} />
                    {t.answer.follow_up_questions.length > 0 && (
                      <div className="follow-ups">
                        {t.answer.follow_up_questions.map((fq, i) => (
                          <button key={i} className="follow-up-btn" onClick={() => pickFollowUp(fq)} disabled={busy}>
                            ↳ {fq}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Input area */}
        <div className="input-area">
          {showContext && (
            <div className="card" style={{ padding: "var(--space-4)", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
              <div className="form-group">
                <label className="form-label">审计项目 ID（可选）</label>
                <input className="input" placeholder="UUID" value={auditCtxId} onChange={(e) => setAuditCtxId(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">审计上下文文本（可选）</label>
                <input className="input" placeholder="单位 / 年度 / 政策清单" value={contextText} onChange={(e) => setContextText(e.target.value)} />
              </div>
            </div>
          )}
          <div className="question-row">
            <textarea
              className="textarea"
              placeholder="输入你的审计问题…（Enter 发送，Shift+Enter 换行）"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
            />
            <button className="btn btn-primary" onClick={submit} disabled={busy || !question.trim()}>
              {busy ? <span className="spinner" /> : "发送"}
            </button>
          </div>
          <div className="flex justify-between items-center">
            <button className="context-toggle" onClick={() => setShowContext((v) => !v)}>
              {showContext ? "▾" : "▸"} 审计上下文
            </button>
            {threadId && <span className="text-muted text-sm">会话: <code>{threadId.slice(0, 12)}…</code></span>}
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
