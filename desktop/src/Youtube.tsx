import { uiText, uiNode, confirmUi, useUiLanguage } from "./i18n";
import { useEffect, useState } from "react";
import { chooseFile, external, request } from "./bridge";
import { StyledSelect } from "./StyledSelect";
import { StatusBadge } from "./StatusBadge";
import { ReportError } from "./Toast";
import type { Publication } from "./types";

export function YoutubeCategorySelect({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const uiLanguage = useUiLanguage();
  const [items, setItems] = useState<{ category_id: string; name: string }[]>(
    [],
  );
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    void request("youtube.auth.status")
      .then(async (status) => {
        if (!status.can_sync) return;
        const result = await request("youtube.categories", {
          ui_language: uiLanguage,
        });
        if (alive) {
          setItems(result.items);
          setError("");
        }
      })
      .catch((e) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, [reload, uiLanguage]);
  return (
    <div>
      <div className="field">
        {uiText("YouTube 分类")}
        <StyledSelect
          label="YouTube 分类"
          value={value}
          disabled={disabled || !items.length}
          onChange={onChange}
          options={[
            ...(!items.some((i) => i.category_id === value)
              ? [{ value, label: uiText("分类 ID {p0}", { p0: value }) }]
              : []),
            ...items.map((i) => ({ value: i.category_id, label: i.name })),
          ]}
        />
      </div>
      <ReportError message={error} />
      {error && (
        <button disabled={disabled} onClick={() => setReload((n) => n + 1)}>
          {uiText("重试读取 YouTube 分类")}
        </button>
      )}
    </div>
  );
}

export function YoutubePrivacySelect({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="field">
      {uiText("YouTube 可见性")}
      <StyledSelect
        label="YouTube 可见性"
        value={value}
        onChange={onChange}
        disabled={disabled}
        options={[
          { value: "private", label: "私密" },
          { value: "unlisted", label: "不公开列出" },
          { value: "public", label: "公开" },
        ]}
      />
    </div>
  );
}

export function YoutubePublicationFields({
  pub,
  busy,
  run,
  onDirtyChange,
}: {
  pub: Publication;
  busy: boolean;
  run: (work: () => Promise<unknown>) => Promise<void>;
  onDirtyChange: (id: string, dirty: boolean) => void;
}) {
  const snapshot = (() => {
    try {
      return JSON.parse(pub.snapshot || "{}");
    } catch {
      return {};
    }
  })();
  const [title, setTitle] = useState(snapshot.title || "");
  const [category, setCategory] = useState(snapshot.category_id || "22");
  const [privacy, setPrivacy] = useState(snapshot.privacy_status || "private");
  const [kids, setKids] = useState(!!snapshot.made_for_kids);
  useEffect(() => {
    setTitle(snapshot.title || "");
    setCategory(snapshot.category_id || "22");
    setPrivacy(snapshot.privacy_status || "private");
    setKids(!!snapshot.made_for_kids);
  }, [pub.snapshot, pub.publication_id]);
  useEffect(() => {
    onDirtyChange(
      pub.publication_id,
      title !== (snapshot.title || "") ||
        category !== (snapshot.category_id || "22") ||
        privacy !== (snapshot.privacy_status || "private") ||
        kids !== !!snapshot.made_for_kids,
    );
  }, [
    title,
    category,
    privacy,
    kids,
    pub.snapshot,
    pub.publication_id,
    onDirtyChange,
  ]);
  const disabled = busy || pub.status !== "ready";
  return (
    <div>
      <p className="help">
        {uiText(
          "YouTube 标题可单独编辑；简介、标签及封面共用本任务的素材。上传后还需等待 YouTube 处理。",
        )}
      </p>
      <label className="field">
        {uiText("YouTube 标题")}
        <input
          value={title}
          maxLength={100}
          disabled={disabled}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <p className="help">
        {uiText("共用简介：")}
        {new TextEncoder().encode(snapshot.description || "").length}
        {uiText(" / 5000 字节 · 共用标签：")}
        {(snapshot.tags || []).join("、")}
      </p>
      <YoutubeCategorySelect
        value={category}
        onChange={setCategory}
        disabled={disabled}
      />
      <YoutubePrivacySelect
        value={privacy}
        onChange={setPrivacy}
        disabled={disabled}
      />
      <label className="checkbox">
        <input
          type="checkbox"
          checked={kids}
          disabled={disabled}
          onChange={(e) => setKids(e.target.checked)}
        />
        {uiText("此视频面向儿童")}
      </label>
      {pub.status === "ready" && (
        <button
          disabled={busy || !title.trim()}
          onClick={() =>
            void run(() =>
              request("publications.update_metadata", {
                publication_id: pub.publication_id,
                revision: pub.revision,
                title,
                category_id: category,
                privacy_status: privacy,
                made_for_kids: kids,
              }),
            )
          }
        >
          {uiText("保存 YouTube 投稿信息")}
        </button>
      )}
      {pub.remote_id && /^[A-Za-z0-9_-]{11}$/.test(pub.remote_id) && (
        <button
          onClick={() =>
            void external(`https://www.youtube.com/watch?v=${pub.remote_id}`)
          }
        >
          {uiText("打开 YouTube 视频")}
        </button>
      )}
      <button onClick={() => void external("https://studio.youtube.com/")}>
        {uiText("打开 YouTube Studio")}
      </button>
    </div>
  );
}

export function YoutubeAccountPanel() {
  const [status, setStatus] = useState<any>(null);
  const [path, setPath] = useState("");
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => setStatus(await request("youtube.auth.status"));
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    if (phase !== "waiting" && phase !== "processing") return;
    let alive = true;
    let timer: number;
    const poll = async () => {
      try {
        const result = await request("youtube.auth.poll");
        if (!alive) return;
        if (["done", "failed", "expired", "idle"].includes(result.status)) {
          setPhase(result.status);
          if (result.error) setError(result.error);
          await refresh();
        } else timer = window.setTimeout(() => void poll(), 1000);
      } catch (e) {
        if (alive) {
          setError(String(e));
          setPhase("failed");
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 500);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [phase]);
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await work();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="settings-card section-body account-connection-panel">
      <div className="status-heading">
        <h3>{uiText("YouTube 同步投稿 · 单频道")}</h3>
        <StatusBadge
          tone={
            !status && !error
              ? "neutral"
              : status?.can_sync
                ? "success"
                : "error"
          }
        >
          {uiNode(
            status?.can_sync
              ? "已连接"
              : status?.account
                ? "需重新授权"
                : status?.configured
                  ? "未授权"
                  : "未配置",
          )}
        </StatusBadge>
      </div>
      <p className="help">
        {uiText(
          "官方 API 接入，独立上传队列。先在 Google Cloud 启用 YouTube Data API v3，创建桌面应用 OAuth 客户端并导入 JSON，再通过系统浏览器授权。授权凭据保存在系统凭据库。",
        )}
      </p>
      <ReportError
        message={
          error ||
          status?.error ||
          (phase === "expired" ? "YouTube 授权已超时，请重试。" : "")
        }
      />
      <p>
        {status?.account
          ? `${status.account.nickname} · ${status.account.channel_id}`
          : uiText("尚未绑定 YouTube 频道")}
      </p>
      {!status?.account && (
        <div className="account-connection-form">
          <label className="field">
            {uiText("OAuth JSON 文件")}
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder={uiText("Google 桌面应用 OAuth JSON 文件的绝对路径")}
            />
          </label>
          <div className="button-row">
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const chosen = await chooseFile(["json"]);
                  if (chosen) setPath(chosen);
                })
              }
            >
              {uiText("选择 OAuth 文件")}
            </button>
            <button
              disabled={busy || !path.trim()}
              onClick={() =>
                void run(async () => {
                  await request("youtube.auth.configure", { path });
                  setPath("");
                })
              }
            >
              {uiText("导入 OAuth 配置")}
            </button>
          </div>
        </div>
      )}
      <div className="modal-actions">
        <button
          disabled={busy || !status?.configured || phase === "waiting"}
          onClick={() =>
            void run(async () => {
              await request("youtube.auth.start");
              setPhase("waiting");
            })
          }
        >
          {uiText("在浏览器授权 YouTube")}
        </button>
        <button
          disabled={busy || !["waiting", "processing"].includes(phase)}
          onClick={() =>
            void run(async () => {
              await request("youtube.auth.cancel");
              setPhase("");
            })
          }
        >
          {uiText("取消 YouTube 授权")}
        </button>
        {status?.account && (
          <>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const next = await request("youtube.auth.status", {
                    verify: true,
                  });
                  if (next.error) throw new Error(next.error);
                })
              }
            >
              {uiText("检查 YouTube 授权")}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(() => request("youtube.accounts.resume_uploads"))
              }
            >
              {uiText("恢复 YouTube 队列")}
            </button>
            <button
              disabled={busy}
              onClick={() => void run(() => request("youtube.auth.clear"))}
            >
              {uiText("退出 YouTube 登录")}
            </button>
            <button
              disabled={busy}
              onClick={() => {
                if (
                  confirmUi(
                    "归档 YouTube 频道并保留历史投稿记录？未完成目标须先处理。",
                  )
                )
                  void run(() => request("youtube.accounts.archive"));
              }}
            >
              {uiText("归档 YouTube 频道")}
            </button>
          </>
        )}
      </div>
      {phase === "waiting" && (
        <p className="help">
          {uiText(
            "请在系统浏览器选择目标频道并允许上传和读取权限，授权完成后此处自动更新。",
          )}
        </p>
      )}
    </section>
  );
}
