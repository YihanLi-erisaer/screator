import { useEffect, useState } from "react";
import { external, request } from "./bridge";
import type { Publication } from "./types";
import { ReportError } from "./Toast";
import { StatusBadge } from "./StatusBadge";

export type XiaohongshuStatus = {
  account: { account_id: string; user_id: string; nickname: string; auth_state: string; binding_revision: number } | null;
  can_sync: boolean; error: string;
};

export function XiaohongshuAccountPanel() {
  const [status, setStatus] = useState<XiaohongshuStatus | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => setStatus(await request("xiaohongshu.auth.status"));
  useEffect(() => { void refresh().catch(e => setError(String(e))); }, []);
  useEffect(() => {
    if (!waiting) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      try {
        const result = await request("xiaohongshu.auth.poll");
        if (stopped) return;
        if (result.status !== "waiting") {
          setWaiting(false); setPhase(result.status);
          await refresh();
        } else timer = window.setTimeout(() => void poll(), 1500);
      } catch (e) {
        if (!stopped) { setError(String(e)); setWaiting(false); }
      }
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [waiting]);
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await work(); await refresh(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  return <section className="settings-card section-body account-connection-panel">
    <div className="status-heading"><h3>小红书图文同步 · 单账号</h3>
      <StatusBadge tone={!status && !error ? "neutral" : status?.can_sync ? "success" : "error"}>{!status ? error ? "检测失败" : "检测中" : status.can_sync ? "已连接" : status.account ? "需重新登录" : "未登录"}</StatusBadge>
    </div>
    <p className="help">在浏览器中登录小红书创作中心。图文同步使用独立队列，共用图片与正文；发布时会打开浏览器。需要安装 Chrome 或 Edge。</p>
    <p>{status?.account ? `${status.account.nickname} · UID ${status.account.user_id}` : "尚未绑定小红书账号"}</p>
    <ReportError message={error || status?.error} />
    {waiting && <p className="help">请在打开的浏览器中完成小红书登录，安全验证也在网页中完成。</p>}
    {phase === "expired" && <p className="help">登录等待已过期，请重新打开浏览器。</p>}
    <div className="modal-actions">
      <button disabled={busy || waiting} onClick={() => void run(async () => { await request("xiaohongshu.auth.start"); setPhase(""); setWaiting(true); })}>登录小红书</button>
      <button disabled={busy || !waiting} onClick={() => void run(async () => { await request("xiaohongshu.auth.cancel"); setWaiting(false); })}>取消小红书登录</button>
      {status?.account && <>
        <button disabled={busy || waiting} onClick={() => void run(async () => {
          const result = await request<XiaohongshuStatus>("xiaohongshu.auth.status", { verify: true });
          setStatus(result);
          if (result.error) throw new Error(result.error);
        })}>检查小红书登录状态</button>
        <button disabled={busy || waiting} onClick={() => void run(() => request("xiaohongshu.accounts.resume_uploads"))}>恢复小红书队列</button>
        <button disabled={busy || waiting} onClick={() => void run(() => request("xiaohongshu.auth.clear"))}>退出小红书登录</button>
        <button disabled={busy || waiting} onClick={() => { if (window.confirm("归档小红书账号并保留历史投稿记录？")) void run(() => request("xiaohongshu.accounts.archive")); }}>归档小红书账号</button>
      </>}
    </div>
  </section>;
}

export function XiaohongshuPublicationFields({ pub, busy, run, onDirtyChange }: {
  pub: Publication; busy: boolean; run: (work: () => Promise<unknown>) => Promise<void>;
  onDirtyChange: (id: string, value: boolean) => void;
}) {
  let snapshot: { title?: string; description?: string } = {};
  try { snapshot = JSON.parse(pub.snapshot || "{}"); } catch { /* display a recoverable empty draft */ }
  const [title, setTitle] = useState(snapshot.title || "");
  useEffect(() => setTitle(snapshot.title || ""), [pub.snapshot, pub.publication_id]);
  useEffect(() => onDirtyChange(pub.publication_id, title !== (snapshot.title || "")), [title, pub.snapshot, pub.publication_id, onDirtyChange]);
  return <div>
    <label className="field">小红书标题 <span>{title.length} / 20</span>
      <input value={title} maxLength={20} disabled={busy || pub.status !== "ready"} onChange={e => setTitle(e.target.value)} />
    </label>
    <p className="help">共用图文正文：{(snapshot.description || "").length} / 1000 字。图片按任务中的顺序发布。</p>
    {pub.status === "ready" && <button disabled={busy || !title.trim() || title === snapshot.title}
      onClick={() => void run(() => request("publications.update_metadata", { publication_id: pub.publication_id, revision: pub.revision, title }))}>保存小红书标题</button>}
    <div className="button-row">
      {pub.remote_id && /^[0-9a-f]{24}$/.test(pub.remote_id) && <button onClick={() => void external(`https://www.xiaohongshu.com/explore/${pub.remote_id}`)}>打开小红书笔记</button>}
      <button onClick={() => void external("https://creator.xiaohongshu.com/creator/notemanage")}>打开小红书创作中心</button>
    </div>
  </div>;
}
