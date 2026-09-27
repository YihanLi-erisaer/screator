import { useEffect, useRef, useState } from "react";
import { external, operationId, request } from "./bridge";

export function AcfunVerification({ publicationId, busy, run }: {
  publicationId: string;
  busy: boolean;
  run: (work: () => Promise<unknown>) => Promise<void>;
}) {
  const [challenge, setChallenge] = useState<{ challenge_id: string; url: string } | null>(null);
  const [error, setError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const completing = useRef(false);
  useEffect(() => {
    if (!challenge) return;
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== new URL(challenge.url).origin || completing.current) return;
      let data;
      try { data = typeof event.data === "string" ? JSON.parse(event.data) : event.data; } catch { return; }
      if (data?.msgType !== "RESULT") return;
      if (data.msg?.result !== 1) {
        setError("验证尚未通过或已取消，请重新打开官方验证。");
        setChallenge(null);
        return;
      }
      completing.current = true;
      void run(async () => {
        try {
          await request("acfun.verification.complete", {
            publication_id: publicationId, challenge_id: challenge.challenge_id,
            verification_type: data.msg.type, token: data.msg.token,
          });
          setChallenge(null);
        } finally { completing.current = false; }
      });
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [challenge, publicationId, run]);
  return <div>
    <p className="help">AcFun 要求手动安全验证。验证完成后请尽快确认投稿，未改变的视频将复用。</p>
    {error && <p className="inline-error">{error}</p>}
    <button disabled={busy || !!challenge} onClick={() => void run(async () => {
      const next = await request("acfun.verification", { publication_id: publicationId });
      if (next.status && next.status !== "ready") {
        setChallenge(null); setError(next.message); setNeedsRefresh(next.status === "refresh_required");
        return;
      }
      const url = new URL(next.url);
      if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !["captcha.zt.kuaishou.com", "captcha.kuaishou.com", "passport.kuaishou.com", "app.m.kuaishou.com"].includes(url.hostname)) {
        throw new Error("不支持此官方验证地址，请到 AcFun 创作中心处理。");
      }
      setError(""); setNeedsRefresh(false); setChallenge(next);
    })}>完成 AcFun 安全验证</button>
    {needsRefresh && <div>
      <p className="help">点击下方按钮会复用素材，仅重试此 AcFun 目标。平台仍要求验证时，请再次点击“完成 AcFun 安全验证”；若平台已放行，本次重试将直接投稿。</p>
      <button disabled={busy} onClick={() => void run(async () => {
        await request("acfun.verification.refresh", { publication_id: publicationId, operation_id: operationId() });
        setNeedsRefresh(false); setError("");
      })}>重新获取验证入口并继续投稿</button>
    </div>}
    <button disabled={busy} onClick={() => void external("https://member.acfun.cn/")}>打开 AcFun 创作中心</button>
    {challenge && <div role="dialog" aria-label="AcFun 官方安全验证">
      <p className="help">请在下方官方页面手动完成验证。如无法显示，可关闭窗口后到创作中心处理。</p>
      <iframe ref={frame} title="AcFun 官方安全验证" src={challenge.url}
        sandbox="allow-scripts allow-same-origin allow-forms" referrerPolicy="no-referrer"
        style={{ width: "100%", maxWidth: 420, height: 420, border: 0, background: "white" }} />
      <button disabled={busy} onClick={() => setChallenge(null)}>关闭验证窗口</button>
    </div>}
  </div>;
}

export function AcfunChannelSelect({ value, onChange, disabled = false, label = "AcFun 分区" }: {
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  label?: string;
}) {
  const [items, setItems] = useState<{ channel_id: number; name: string }[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    request("acfun.channels").then((result) => {
      if (alive) setItems(result.items);
    }).catch((e) => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);
  const invalid = value > 0 && !loading && !error && !items.some((item) => item.channel_id === value);
  return <div>
    <label className="field">{label}
      <select value={value} disabled={disabled || loading || !items.length} onChange={(e) => onChange(Number(e.target.value))}>
        <option value={0}>{loading ? "正在读取 AcFun 分区…" : "请选择具体分区"}</option>
        {value > 0 && !items.some((item) => item.channel_id === value) && <option value={value} disabled>未匹配到可投稿分区（ID {value}）</option>}
        {items.map((item) => <option key={item.channel_id} value={item.channel_id}>{item.name}（{item.channel_id}）</option>)}
      </select>
    </label>
    {invalid && <p className="inline-error">该 ID 不是可投稿的视频子分区，请重新选择。</p>}
    {error && <p className="help">{error} <button type="button" disabled={disabled || loading} onClick={() => setReload((n) => n + 1)}>重试读取分区</button></p>}
  </div>;
}

export function AcfunAccountPanel() {
  const [status, setStatus] = useState<any>(null);
  const [qr, setQr] = useState("");
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async (verify = false) => setStatus(await request("acfun.auth.status", { verify }));
  useEffect(() => { void refresh().catch((e) => setError(String(e))); }, []);
  useEffect(() => {
    if (!qr) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      try {
        const value: any = await request("acfun.auth.poll");
        if (stopped) return;
        setPhase(value.status);
        if (["done", "expired", "failed"].includes(value.status)) {
          setQr("");
          void refresh();
        } else {
          timer = window.setTimeout(() => void poll(), 1000);
        }
      } catch (e) {
        if (!stopped) { setError(String(e)); setQr(""); }
      }
    };
    timer = window.setTimeout(() => void poll(), 0);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [qr]);
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await work(); await refresh(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  return <section className="section-body">
    <h3>AcFun 同步投稿 · 单账号</h3>
    <p className="help">实验性网页接入，独立队列。平台接口尚需真实账号验证；启用前请确认你接受网页接口变化的风险。</p>
    {error && <p className="inline-error">{error}</p>}
    {status?.error && <p className="help">{status.error}</p>}
    <p>{status?.account ? `${status.account.nickname} · UID ${status.account.user_id} · ${status.account.auth_state === "valid" ? "已登录" : "需重新扫码"}` : "尚未绑定 AcFun 账号"}</p>
    <label className="checkbox"><input type="checkbox" checked={!!status?.enabled} disabled={busy}
      onChange={(e) => void run(() => request("settings.update", { values: { acfun_experimental_enabled: e.target.checked } }))} />启用 AcFun 实验性接入</label>
    <div className="modal-actions">
      <button disabled={busy} onClick={() => void run(async () => {
        const result = await request("acfun.auth.start");
        setQr(result.qrcode); setPhase(result.status);
      })}>扫码登录 AcFun</button>
      <button disabled={busy || !qr} onClick={() => void run(async () => {
        await request("acfun.auth.cancel"); setQr(""); setPhase("");
      })}>取消扫码</button>
      {status?.account && <>
        <button disabled={busy} onClick={() => void run(() => request("acfun.auth.status", { verify: true }))}>检查登录状态</button>
        <button disabled={busy} onClick={() => void run(() => request("acfun.accounts.resume_uploads"))}>恢复 AcFun 队列</button>
        <button disabled={busy} onClick={() => void run(() => request("acfun.auth.clear"))}>退出登录</button>
        <button disabled={busy} onClick={() => { if (window.confirm("归档 AcFun 账号并保留历史投稿记录？")) void run(() => request("acfun.accounts.archive")); }}>归档账号</button>
      </>}
    </div>
    {qr && <div><img alt="AcFun 登录二维码" width={220} height={220} src={qr.startsWith("data:") ? qr : `data:image/png;base64,${qr}`} /><p className="help">{phase === "scanned" ? "已扫码，请在手机确认。" : "请用 AcFun 扫码并在手机确认。"}</p></div>}
    {phase === "expired" && <p className="inline-error">二维码已过期，请重新获取。</p>}
  </section>;
}
