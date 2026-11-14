import { useCallback, useEffect, useRef, useState } from "react";
import { listDocuments, retryDocument, uploadDocument } from "../api";
import type { DocumentInfo, IngestResponse } from "../types";

// ── Status badge ────────────────────────────────────────────
function StatusBadge({ status }: { status: DocumentInfo["status"] }) {
  const labels: Record<string, string> = {
    ready: "已就绪",
    failed: "失败",
    parsing: "解析中",
    indexing: "索引中",
    uploaded: "已上传",
  };
  return (
    <span className={`badge badge-${status}`}>{labels[status] ?? status}</span>
  );
}

// ── Upload modal ─────────────────────────────────────────────
interface UploadState {
  files: File[];
  docType: string;
  department: string;
  auditYear: string;
  auditCtxId: string;
  busy: boolean;
  results: Array<{ name: string; ok: boolean; msg: string }>;
}

function UploadPanel({ onDone }: { onDone: () => void }) {
  const [s, setS] = useState<UploadState>({
    files: [],
    docType: "",
    department: "",
    auditYear: "",
    auditCtxId: "",
    busy: false,
    results: [],
  });
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFiles(list: FileList | null) {
    if (!list) return;
    setS((prev) => ({
      ...prev,
      files: [...prev.files, ...Array.from(list)],
      results: [],
    }));
  }

  function removeFile(idx: number) {
    setS((prev) => ({
      ...prev,
      files: prev.files.filter((_, i) => i !== idx),
    }));
  }

  async function submit() {
    if (!s.files.length || s.busy) return;
    setS((prev) => ({ ...prev, busy: true, results: [] }));
    const results: UploadState["results"] = [];
    for (const file of s.files) {
      try {
        const r: IngestResponse = await uploadDocument(file, {
          doc_type: s.docType || undefined,
          department: s.department || undefined,
          audit_year: s.auditYear ? parseInt(s.auditYear) : undefined,
          audit_ctx_id: s.auditCtxId || undefined,
        });
        results.push({
          name: file.name,
          ok: true,
          msg: `文档 ID: ${r.document_id}，状态: ${r.status}，chunks: ${r.chunks}`,
        });
      } catch (e) {
        results.push({ name: file.name, ok: false, msg: String(e) });
      }
    }
    setS((prev) => ({ ...prev, busy: false, results, files: prev.files.filter((_, i) => !results[i]?.ok) }));
    if (results.every((r) => r.ok)) onDone();
  }

  return (
    <div className="card" style={{ marginBottom: "var(--space-5)" }}>
      <div className="card-header">
        <strong>上传文档</strong>
        <span className="text-muted text-sm">支持 PDF、Word、PPT、图片、HTML、xlsx/xlsb/csv</span>
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        {/* Drop zone */}
        <div
          className={`upload-zone${drag ? " drag-over" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
        >
          <input ref={inputRef} type="file" multiple onChange={(e) => addFiles(e.target.files)} />
          <div className="upload-icon">📂</div>
          <div>点击或拖拽文件到此处</div>
          <div className="upload-hint">可一次上传多个文件</div>
        </div>

        {/* File list */}
        {s.files.length > 0 && (
          <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            {s.files.map((f, i) => (
              <li key={i} className="flex items-center gap-2" style={{ justifyContent: "space-between", background: "var(--color-surface-raised)", padding: "6px 12px", borderRadius: "var(--radius-sm)" }}>
                <span className="text-sm">📄 {f.name} <span className="text-muted">({(f.size / 1024).toFixed(0)} KB)</span></span>
                <button className="btn btn-ghost btn-sm btn-icon" onClick={() => removeFile(i)} title="移除">✕</button>
              </li>
            ))}
          </ul>
        )}

        {/* Meta fields */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
          <div className="form-group">
            <label className="form-label">文档类型（可选）</label>
            <input className="input" placeholder="regulation / contract …" value={s.docType} onChange={(e) => setS((p) => ({ ...p, docType: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">部门（可选）</label>
            <input className="input" placeholder="IT / Finance …" value={s.department} onChange={(e) => setS((p) => ({ ...p, department: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">审计年度（可选）</label>
            <input className="input" type="number" placeholder="2025" value={s.auditYear} onChange={(e) => setS((p) => ({ ...p, auditYear: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">审计项目 ID（可选）</label>
            <input className="input" placeholder="UUID" value={s.auditCtxId} onChange={(e) => setS((p) => ({ ...p, auditCtxId: e.target.value }))} />
          </div>
        </div>

        <div>
          <button className="btn btn-primary" onClick={submit} disabled={s.busy || !s.files.length}>
            {s.busy ? <><span className="spinner" /> 上传中…</> : "上传并入库"}
          </button>
        </div>

        {/* Results */}
        {s.results.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            {s.results.map((r, i) => (
              <div key={i} className={`alert alert-${r.ok ? "success" : "error"}`}>
                <strong>{r.name}</strong>：{r.msg}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────
export default function KnowledgePage() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDocs(await listDocuments());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Auto-refresh while any doc is in progress
  useEffect(() => {
    const pending = docs.some((d) => d.status === "parsing" || d.status === "indexing" || d.status === "uploaded");
    if (!pending) return;
    const id = setTimeout(refresh, 3000);
    return () => clearTimeout(id);
  }, [docs, refresh]);

  async function handleRetry(id: string) {
    setRetrying(id);
    try {
      await retryDocument(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setRetrying(null);
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3 justify-between" style={{ marginBottom: "var(--space-5)" }}>
        <h1 className="page-title" style={{ margin: 0 }}>知识库管理</h1>
        <div className="flex gap-2">
          <button className="btn btn-outline" onClick={refresh} disabled={loading}>
            {loading ? <span className="spinner" /> : "🔄"} 刷新
          </button>
          <button className="btn btn-primary" onClick={() => setShowUpload((v) => !v)}>
            {showUpload ? "收起上传" : "＋ 上传文档"}
          </button>
        </div>
      </div>

      {showUpload && (
        <UploadPanel onDone={() => { setShowUpload(false); refresh(); }} />
      )}

      {error && <div className="alert alert-error" style={{ marginBottom: "var(--space-4)" }}>{error}</div>}

      <div className="card">
        <div className="card-header">
          <strong>已入库文档</strong>
          <span className="text-muted text-sm">{docs.length} 条记录</span>
        </div>
        <div className="table-wrap">
          {loading && docs.length === 0 ? (
            <div style={{ padding: "var(--space-8)", display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {[1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 10}%` }} />)}
            </div>
          ) : docs.length === 0 ? (
            <div className="empty-state">暂无文档，请上传文件</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th>状态</th>
                  <th>Chunks</th>
                  <th>页数</th>
                  <th>文档 ID</th>
                  <th>错误信息</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {d.filename ?? "—"}
                    </td>
                    <td>
                      <StatusBadge status={d.status} />
                      {(d.status === "parsing" || d.status === "indexing") && (
                        <span className="spinner" style={{ marginLeft: "var(--space-2)" }} />
                      )}
                    </td>
                    <td style={{ fontVariantNumeric: "tabular-nums" }}>
                      {(d.parse_meta as { chunks?: number } | null)?.chunks ?? "—"}
                    </td>
                    <td style={{ fontVariantNumeric: "tabular-nums" }}>
                      {(d.parse_meta as { pages?: number } | null)?.pages ?? "—"}
                    </td>
                    <td>
                      <code style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>{d.id.slice(0, 16)}…</code>
                    </td>
                    <td style={{ maxWidth: 220, color: "var(--color-danger)", fontSize: "var(--font-size-sm)" }}>
                      {d.error ? (
                        <span title={d.error}>{d.error.slice(0, 60)}{d.error.length > 60 ? "…" : ""}</span>
                      ) : "—"}
                    </td>
                    <td>
                      {d.status === "failed" && (
                        <button
                          className="btn btn-outline btn-sm"
                          onClick={() => handleRetry(d.id)}
                          disabled={retrying === d.id}
                        >
                          {retrying === d.id ? <span className="spinner" /> : "↺"} 重新索引
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
