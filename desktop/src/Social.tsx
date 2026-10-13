import { uiNode, uiText, confirmUi } from "./i18n";
import { useEffect, useState } from "react";
import { external, request } from "./bridge";
import { StyledSelect } from "./StyledSelect";
import { StatusBadge } from "./StatusBadge";
import { ReportError } from "./Toast";
import type { Publication } from "./types";

export const socialPlatforms = ["twitter", "facebook", "tiktok"] as const;
export type SocialPlatform = (typeof socialPlatforms)[number];
export const socialNames = {
  twitter: "X（Twitter）",
  facebook: "Facebook Page",
  tiktok: "TikTok",
};
export type SocialTarget = {
  platform: SocialPlatform;
  account_id: string;
  binding_revision: number;
};

function SocialAccountPanel({ platform }: { platform: SocialPlatform }) {
  const [status, setStatus] = useState<any>(null);
  const [token, setToken] = useState("");
  const [pageId, setPageId] = useState("");
  const [version, setVersion] = useState("v25.0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () =>
    setStatus(await request("social.auth.status", { platform }));
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, [platform]);
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
    <section className="settings-card section-body social-account-panel">
      <div className="status-heading">
        <h3>
          {uiNode(socialNames[platform])}
          {uiText(" · 单账号同步投稿")}
        </h3>
        <StatusBadge
          tone={
            status?.can_sync
              ? "success"
              : !status && !error
                ? "neutral"
                : "error"
          }
        >
          {uiNode(
            status?.can_sync
              ? "已连接"
              : status?.account
                ? "需重新授权"
                : "未绑定",
          )}
        </StatusBadge>
      </div>
      <p className="help">
        {uiText(
          "使用官方 API 的用户授权凭据，保存在系统凭据库。网站 Cookie 不能用于此连接；账号与应用须具有对应的发布权限。",
        )}
      </p>
      {platform === "twitter" && (
        <p className="help">
          {uiText(
            "需 X 用户访问凭据，具备 users.read、tweet.read、tweet.write 和 media.write 权限；应用需有相应 API 访问额度。",
          )}
        </p>
      )}
      {platform === "facebook" && (
        <p className="help">
          {uiText(
            "仅发布到公共主页（Page）。需 Page Access Token 及 pages_manage_posts、pages_read_engagement 权限。",
          )}
        </p>
      )}
      {platform === "tiktok" && (
        <p className="help">
          {uiText(
            "需 user.info.basic 与 video.publish 授权。TikTok 必须逐次预览确认；公开发布受应用审核及账号权限限制。",
          )}
        </p>
      )}
      <ReportError message={error || status?.error} />
      {status?.account && (
        <p className="social-account-identity" data-no-localize>
          {status.account.nickname} · {status.account.subject_id}
        </p>
      )}
      <div className="task-form-fields">
        {platform === "facebook" && (
          <>
            <label className="field">
              Facebook Page ID
              <input
                value={pageId}
                disabled={busy}
                onChange={(e) => setPageId(e.target.value)}
                placeholder={uiText("公共主页的数字 ID")}
              />
            </label>
            <label className="field">
              {uiText("Graph API 版本")}
              <input
                value={version}
                disabled={busy}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="v25.0"
              />
            </label>
          </>
        )}
        <label className="field">
          {uiText("用户授权凭据")}
          <input
            type="password"
            value={token}
            disabled={busy}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Access Token"
          />
        </label>
      </div>
      <div className="modal-actions social-account-actions">
        <button
          disabled={
            busy || !token.trim() || (platform === "facebook" && !pageId.trim())
          }
          onClick={() =>
            void run(async () => {
              await request("social.auth.bind", {
                platform,
                access_token: token.trim(),
                page_id: pageId.trim(),
                api_version: version,
              });
              setToken("");
            })
          }
        >
          {uiText("验证并绑定账号")}
        </button>
        {status?.account && (
          <>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const next = await request("social.auth.status", {
                    platform,
                    verify: true,
                  });
                  if (next.error) throw new Error(next.error);
                })
              }
            >
              {uiText("检查授权")}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(() =>
                  request("social.accounts.resume_uploads", { platform }),
                )
              }
            >
              {uiText("恢复平台队列")}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(() => request("social.auth.clear", { platform }))
              }
            >
              {uiText("清除授权凭据")}
            </button>
            <button
              disabled={busy}
              onClick={() => {
                if (
                  confirmUi(
                    "归档此平台账号并保留历史记录？未完成目标须先处理。",
                  )
                )
                  void run(() =>
                    request("social.accounts.archive", { platform }),
                  );
              }}
            >
              {uiText("归档平台账号")}
            </button>
          </>
        )}
      </div>
    </section>
  );
}

export function SocialAccountPanels() {
  return (
    <>
      {socialPlatforms.map((platform) => (
        <SocialAccountPanel key={platform} platform={platform} />
      ))}
    </>
  );
}

export function SocialTargets({
  mode,
  value,
  onChange,
}: {
  mode: string;
  value: SocialTarget[];
  onChange: (value: SocialTarget[]) => void;
}) {
  const [statuses, setStatuses] = useState<Record<string, any>>({});
  useEffect(() => {
    let alive = true;
    socialPlatforms.forEach((platform) => {
      void request("social.auth.status", { platform, verify: true })
        .then((status) => {
          if (alive)
            setStatuses((current) => ({ ...current, [platform]: status }));
        })
        .catch(() => {});
    });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <div className="social-targets">
      {socialPlatforms.map((platform) => {
        const status = statuses[platform];
        const selected = value.some((target) => target.platform === platform);
        return (
          <div key={platform}>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={selected}
                disabled={
                  !status?.can_sync ||
                  (platform === "tiktok" && mode === "auto" && !selected)
                }
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [
                          ...value,
                          {
                            platform,
                            account_id: status.account.account_id,
                            binding_revision: status.account.binding_revision,
                          },
                        ]
                      : value.filter((target) => target.platform !== platform),
                  )
                }
              />
              {uiText("同步上传 ")}
              {uiNode(socialNames[platform])}
              {status?.account ? ` · ${status.account.nickname}` : ""}
            </label>
            <p className="help">
              {uiNode(
                platform === "tiktok" && mode === "auto"
                  ? "TikTok 需要逐次预览确认，请切换为预览模式。"
                  : status?.can_sync
                    ? "共用视频素材，独立排队和记录结果。"
                    : "请先在“账号与连接”绑定此平台账号。",
              )}
            </p>
          </div>
        );
      })}
    </div>
  );
}

const metadataKeys = [
  "title",
  "privacy_level",
  "allow_comment",
  "allow_duet",
  "allow_stitch",
  "content_disclosure",
  "brand_organic",
  "brand_content",
  "is_aigc",
  "consent",
  "original_content",
  "cover_timestamp_ms",
];
function draftOf(pub: Publication) {
  let value: any = {};
  try {
    value = JSON.parse(pub.snapshot || "{}");
  } catch {}
  return Object.fromEntries(
    metadataKeys.map((key) => [
      key,
      value[key] ??
        (key === "title" || key === "privacy_level"
          ? ""
          : key === "cover_timestamp_ms"
            ? 0
            : false),
    ]),
  );
}
const privacyNames: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "所有人",
  FOLLOWER_OF_CREATOR: "关注者",
  MUTUAL_FOLLOW_FRIENDS: "互相关注的好友",
  SELF_ONLY: "仅自己",
};

export function SocialPublicationFields({
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
  const platform = pub.platform as SocialPlatform;
  const [draft, setDraft] = useState(draftOf(pub));
  const [creator, setCreator] = useState<any>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => setDraft(draftOf(pub)), [pub.snapshot, pub.publication_id]);
  useEffect(() => {
    if (platform !== "tiktok" || pub.status !== "ready") return;
    let alive = true;
    void request("social.creator_info", {
      platform,
      account_id: pub.account_id,
    })
      .then((value) => {
        if (!alive) return;
        setCreator(value);
        setError("");
        setDraft((current) => ({
          ...current,
          privacy_level: value.privacy_level_options.includes(
            current.privacy_level,
          )
            ? current.privacy_level
            : "",
          allow_comment: !value.comment_disabled && current.allow_comment,
          allow_duet: !value.duet_disabled && current.allow_duet,
          allow_stitch: !value.stitch_disabled && current.allow_stitch,
        }));
      })
      .catch((e) => {
        if (alive) {
          setError(String(e));
          setCreator(null);
        }
      });
    return () => {
      alive = false;
    };
  }, [platform, pub.publication_id, pub.status, reload]);
  useEffect(
    () =>
      onDirtyChange(
        pub.publication_id,
        JSON.stringify(draft) !== JSON.stringify(draftOf(pub)),
      ),
    [draft, pub.snapshot, pub.publication_id, onDirtyChange],
  );
  const update = (key: string, value: unknown) =>
    setDraft((current) => ({ ...current, consent: false, [key]: value }));
  const disabled = busy || pub.status !== "ready";
  return (
    <div className="task-form-fields social-publication">
      <label className="field">
        {uiText("平台标题或视频文案")}
        <textarea
          value={String(draft.title)}
          maxLength={
            platform === "twitter" ? 280 : platform === "facebook" ? 255 : 2200
          }
          disabled={disabled}
          onChange={(e) => update("title", e.target.value)}
        />
      </label>
      {platform === "facebook" && (
        <p className="help">
          {uiText(
            "Facebook 标题可单独编辑，简介共用任务简介。上传后仍需等待平台处理视频。",
          )}
        </p>
      )}
      {platform === "twitter" && (
        <p className="help">
          {uiText(
            "X 的文案可单独编辑，字符权重及账号发布限制由平台校验。视频处理完成后才创建帖子。",
          )}
        </p>
      )}
      {platform === "tiktok" && (
        <>
          <ReportError message={error} />
          {error && (
            <button disabled={disabled} onClick={() => setReload((n) => n + 1)}>
              {uiText("重新读取 TikTok 权限")}
            </button>
          )}
          {creator && (
            <p className="help">
              {uiText("TikTok 创作者：")}
              {creator.creator_nickname}
              {uiText(" · 最长视频：")}
              {uiNode(creator.max_video_post_duration_sec)}
              {uiText(" 秒")}
            </p>
          )}
          <button
            disabled={busy}
            onClick={() =>
              void run(() =>
                request("social.publications.preview", {
                  publication_id: pub.publication_id,
                }),
              )
            }
          >
            {uiText("预览投稿视频")}
          </button>
          <div className="field">
            {uiText("TikTok 可见性")}
            <StyledSelect
              label="TikTok 可见性"
              value={String(draft.privacy_level)}
              disabled={disabled || !creator}
              options={[
                { value: "", label: "请手动选择可见性" },
                ...(creator?.privacy_level_options || []).map(
                  (level: string) => ({
                    value: level,
                    label: privacyNames[level] || level,
                    disabled: level === "SELF_ONLY" && !!draft.brand_content,
                  }),
                ),
              ]}
              onChange={(value) => update("privacy_level", value)}
            />
          </div>
          <div className="social-interactions">
            {[
              ["allow_comment", "comment_disabled", "允许评论"],
              ["allow_duet", "duet_disabled", "允许合拍"],
              ["allow_stitch", "stitch_disabled", "允许拼接"],
            ].map(([key, unavailable, label]) => (
              <label className="checkbox" key={key}>
                <input
                  type="checkbox"
                  checked={!!draft[key]}
                  disabled={disabled || !creator || !!creator[unavailable]}
                  onChange={(e) => update(key, e.target.checked)}
                />
                {uiNode(label)}
              </label>
            ))}
          </div>
          <label className="field">
            {uiText("TikTok 封面帧位置（秒）")}
            <input
              type="number"
              min={0}
              step={0.1}
              value={Number(draft.cover_timestamp_ms) / 1000}
              disabled={disabled}
              onChange={(e) =>
                update(
                  "cover_timestamp_ms",
                  Math.round(Number(e.target.value) * 1000),
                )
              }
            />
          </label>
          <p className="help">
            {uiText(
              "TikTok 封面取自视频帧，不能直接使用本任务的独立封面图片。",
            )}
          </p>
          <div className="social-interactions">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!draft.content_disclosure}
                disabled={disabled}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    content_disclosure: e.target.checked,
                    brand_organic: false,
                    brand_content: false,
                    consent: false,
                  }))
                }
              />
              {uiText("此视频包含推广内容")}
            </label>
            {!!draft.content_disclosure && (
              <>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={!!draft.brand_organic}
                    disabled={disabled}
                    onChange={(e) => update("brand_organic", e.target.checked)}
                  />
                  {uiText("推广自己的品牌或业务")}
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={!!draft.brand_content}
                    disabled={disabled}
                    onChange={(e) => update("brand_content", e.target.checked)}
                  />
                  {uiText("推广第三方品牌（付费合作）")}
                </label>
              </>
            )}
            <ReportError
              message={
                draft.brand_content && draft.privacy_level === "SELF_ONLY"
                  ? "品牌合作内容不能选择仅自己可见，请重新选择可见性。"
                  : null
              }
            />
            {(!!draft.brand_organic || !!draft.brand_content) && (
              <p className="help">
                {uiNode(
                  draft.brand_content
                    ? "视频将标记为付费合作。"
                    : "视频将标记为推广内容。",
                )}
              </p>
            )}
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!draft.is_aigc}
                disabled={disabled}
                onChange={(e) => update("is_aigc", e.target.checked)}
              />
              {uiText("此视频包含 AI 生成内容")}
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!draft.original_content}
                disabled={disabled}
                onChange={(e) => update("original_content", e.target.checked)}
              />
              {uiText("这是我创作的原创内容")}
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!draft.consent}
                disabled={disabled}
                onChange={(e) => update("consent", e.target.checked)}
              />
              {uiNode(
                draft.brand_content
                  ? "发布即表示我同意 TikTok 品牌内容政策和音乐使用确认，并确认上传此视频。"
                  : "发布即表示我同意 TikTok 音乐使用确认，并确认上传此视频。",
              )}
            </label>
            <button
              type="button"
              onClick={() =>
                void external(
                  "https://developers.tiktok.com/docs/en/content-sharing-guidelines",
                )
              }
            >
              {uiText("查看 TikTok 发布要求")}
            </button>
          </div>
        </>
      )}
      {pub.status === "ready" && (
        <div className="button-row">
          <button
            disabled={
              busy ||
              !String(draft.title).trim() ||
              (platform === "tiktok" &&
                (!creator ||
                  !draft.privacy_level ||
                  (draft.content_disclosure &&
                    !draft.brand_organic &&
                    !draft.brand_content) ||
                  (draft.brand_content && draft.privacy_level === "SELF_ONLY")))
            }
            onClick={() =>
              void run(() =>
                request("publications.update_metadata", {
                  publication_id: pub.publication_id,
                  revision: pub.revision,
                  social_metadata: draft,
                }),
              )
            }
          >
            {uiText("保存平台投稿信息")}
          </button>
        </div>
      )}
      {pub.status === "submission_unknown" && platform !== "twitter" && (
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const result = await request("social.publications.check", {
                publication_id: pub.publication_id,
              });
              if (result.status === "processing")
                throw new Error("平台仍在处理，请稍后再次核对，不能重新上传。");
            })
          }
        >
          {uiText("查询原发布状态")}
        </button>
      )}
      {pub.remote_id &&
        /^\d+$/.test(pub.remote_id) &&
        platform !== "tiktok" && (
          <button
            onClick={() =>
              void external(
                platform === "twitter"
                  ? `https://x.com/i/web/status/${pub.remote_id}`
                  : `https://www.facebook.com/watch/?v=${pub.remote_id}`,
              )
            }
          >
            {uiText("打开平台作品")}
          </button>
        )}
    </div>
  );
}
