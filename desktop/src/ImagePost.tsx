import { uiNode, uiText } from "./i18n";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  FolderOpen,
  ImagePlus,
  Loader2,
  Trash2,
} from "lucide-react";
import { AccountSelector } from "./Accounts";
import { chooseFiles, operationId, request } from "./bridge";
import { bytes, type BiliAccount } from "./types";
import type { XiaohongshuStatus } from "./Xiaohongshu";
import { ReportError } from "./Toast";

export type PostImage = {
  path: string;
  name: string;
  image: string;
  width: number;
  height: number;
  size: number;
};
type Action = (work: () => Promise<unknown>, success?: string) => Promise<void>;

export function PostImages({
  images,
  disabled,
  onChange,
}: {
  images: PostImage[];
  disabled?: boolean;
  onChange?: (images: PostImage[]) => void;
}) {
  const move = (index: number, direction: number) => {
    const next = [...images];
    [next[index], next[index + direction]] = [
      next[index + direction],
      next[index],
    ];
    onChange?.(next);
  };
  return (
    <div className="post-image-grid">
      {images.map((image, index) => (
        <figure className="post-image-card" key={image.path}>
          <img src={image.image} alt={image.name} />
          <figcaption>
            <strong data-no-localize>
              {index + 1}. {image.name}
            </strong>
            <small>
              {image.width} × {image.height} · {uiNode(bytes(image.size))}
            </small>
          </figcaption>
          {onChange && (
            <div className="post-image-actions">
              <button
                className="icon-button"
                aria-label={uiText(uiText("前移图片 {p0}", { p0: index + 1 }))}
                disabled={disabled || index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowLeft size={15} />
              </button>
              <button
                className="icon-button"
                aria-label={uiText(uiText("后移图片 {p0}", { p0: index + 1 }))}
                disabled={disabled || index === images.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowRight size={15} />
              </button>
              <button
                className="icon-button"
                aria-label={uiText(uiText("移除图片 {p0}", { p0: index + 1 }))}
                disabled={disabled}
                onClick={() => onChange(images.filter((_, i) => i !== index))}
              >
                <Trash2 size={15} />
              </button>
            </div>
          )}
        </figure>
      ))}
    </div>
  );
}

export function ImagePostForm({
  accounts,
  busy,
  action,
  close,
  done,
}: {
  accounts: BiliAccount[];
  busy: boolean;
  action: Action;
  close: () => void;
  done: () => Promise<void>;
}) {
  const [images, setImages] = useState<PostImage[]>([]);
  const [path, setPath] = useState("");
  const [text, setText] = useState("");
  const [accountId, setAccountId] = useState("");
  const [mode, setMode] = useState("preview");
  const [authorized, setAuthorized] = useState(false);
  const [xiaohongshu, setXiaohongshu] = useState<XiaohongshuStatus | null>(
    null,
  );
  const [syncXiaohongshu, setSyncXiaohongshu] = useState(false);
  const [xhsTitle, setXhsTitle] = useState("");
  const [xhsError, setXhsError] = useState("");
  const op = useRef(operationId());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let stopped = false;
    request<XiaohongshuStatus>("xiaohongshu.auth.status", { verify: true })
      .then((value) => {
        if (!stopped) setXiaohongshu(value);
      })
      .catch((e) => {
        if (!stopped) setXhsError(String(e));
      });
    return () => {
      stopped = true;
    };
  }, []);
  const textLimit = syncXiaohongshu ? 1000 : 2000;
  const invalidXhs =
    syncXiaohongshu &&
    (!xiaohongshu?.can_sync ||
      !xhsTitle.trim() ||
      text.length > 1000 ||
      images.some((image) => /\.gif$/i.test(image.name)));
  const change = (next: PostImage[]) => {
    setImages(next);
    op.current = operationId();
  };
  const add = async (paths: string[]) => {
    const next = [...new Set([...images.map((image) => image.path), ...paths])];
    if (next.length > 9) throw new Error("每条图文动态最多上传 9 张图片。");
    const result = await request<{ items: PostImage[] }>(
      "image_posts.inspect",
      { paths: next },
    );
    if (alive.current) {
      change(result.items);
      setPath("");
    }
  };
  return (
    <>
      <p className="modal-copy">
        {uiText("发布到 Bilibili 图文动态，按图片顺序展示正文与配图。")}
      </p>
      <div className="post-image-heading">
        <strong>
          {uiText("图片 · ")}
          {images.length} / 9
        </strong>
        <button
          className="secondary"
          disabled={busy || images.length >= 9}
          onClick={() =>
            void action(async () => {
              const paths = await chooseFiles(["jpg", "jpeg", "png", "gif"]);
              if (paths?.length) await add(paths);
            })
          }
        >
          <ImagePlus size={16} />
          {uiText("选择图片")}
        </button>
      </div>
      <p className="help">
        {uiText(
          "支持 JPG、PNG、GIF，单张不超过 10 MB。可多选图片，并调整发布顺序。",
        )}
      </p>
      {images.length ? (
        <PostImages images={images} disabled={busy} onChange={change} />
      ) : (
        <div className="post-image-empty">
          <ImagePlus size={28} />
          <span>{uiText("添加图片开始创作")}</span>
        </div>
      )}
      <div className="task-form-fields">
        <div className="file-field">
          <label className="field">
            {uiText("图片文件路径")}
            <input
              value={path}
              disabled={busy}
              placeholder={uiText("也可输入本地图片绝对路径")}
              onChange={(e) => setPath(e.target.value)}
            />
          </label>
          <div className="button-row">
            <button
              className="secondary"
              disabled={busy || !path.trim() || images.length >= 9}
              onClick={() => void action(() => add([path.trim()]))}
            >
              <FolderOpen size={15} />
              {uiText("添加此图片")}
            </button>
          </div>
        </div>
        <label className="field">
          {uiText("图文正文 ")}
          <span>
            {text.length} / {textLimit}
          </span>
          <textarea
            rows={5}
            maxLength={textLimit}
            value={text}
            disabled={busy}
            placeholder={uiText("分享你的想法…")}
            onChange={(e) => {
              setText(e.target.value);
              op.current = operationId();
            }}
          />
        </label>
        <AccountSelector
          accounts={accounts}
          value={accountId}
          onChange={(id) => {
            setAccountId(id);
            op.current = operationId();
          }}
        />
        {!accounts.some((account) => account.lifecycle === "active") && (
          <p className="help">
            {uiText("请先到账号与连接添加 Bilibili 账号。")}
          </p>
        )}
      </div>
      <div className="social-targets">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={syncXiaohongshu}
            disabled={busy || !xiaohongshu?.can_sync}
            onChange={(e) => {
              setSyncXiaohongshu(e.target.checked);
              op.current = operationId();
            }}
          />
          {uiText("同步上传小红书")}
          {xiaohongshu?.account ? ` · ${xiaohongshu.account.nickname}` : ""}
        </label>
        {!xiaohongshu?.can_sync && (
          <p className="help">
            {uiText("请先到账号与连接登录小红书，登录后可选择同步上传。")}
          </p>
        )}
        <ReportError message={xhsError || xiaohongshu?.error} />
        {syncXiaohongshu && (
          <>
            <label className="field">
              {uiText("小红书标题 ")}
              <span>{xhsTitle.length} / 20</span>
              <input
                maxLength={20}
                value={xhsTitle}
                disabled={busy}
                onChange={(e) => {
                  setXhsTitle(e.target.value);
                  op.current = operationId();
                }}
              />
            </label>
            <p className="help">
              {uiText(
                "共用图片与正文，小红书独立排队。标题最多 20 字，正文最多 1000 字，图片支持 JPG、PNG。",
              )}
            </p>
            {text.length > 1000 && (
              <p className="help error-text" role="alert">
                {uiText(
                  "同步小红书时正文不能超过 1000 字，请缩短正文或取消同步。",
                )}
              </p>
            )}
            {images.some((image) => /\.gif$/i.test(image.name)) && (
              <p className="help error-text" role="alert">
                {uiText("同步小红书不支持 GIF，请替换图片或取消同步。")}
              </p>
            )}
          </>
        )}
      </div>
      <div className="mode-options">
        <label className={mode === "preview" ? "chosen" : ""}>
          <input
            type="radio"
            name="image-mode"
            checked={mode === "preview"}
            disabled={busy}
            onChange={() => {
              setMode("preview");
              op.current = operationId();
            }}
          />
          <span>
            <strong>
              {uiText("准备图片并预览 ")}
              <em>{uiText("推荐")}</em>
            </strong>
            <small>{uiText("校验图片后，由你确认发布。")}</small>
          </span>
        </label>
        <label className={mode === "auto" ? "chosen" : ""}>
          <input
            type="radio"
            name="image-mode"
            checked={mode === "auto"}
            disabled={busy}
            onChange={() => {
              setMode("auto");
              op.current = operationId();
            }}
          />
          <span>
            <strong>{uiText("自动发布动态")}</strong>
            <small>{uiText("图片准备好后，自动提交到所选账号。")}</small>
          </span>
        </label>
      </div>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={authorized}
          disabled={busy}
          onChange={(e) => setAuthorized(e.target.checked)}
        />
        {uiText("我拥有这些图片与文字的版权或已获得发布授权。")}
      </label>
      <div className="modal-actions">
        <button className="secondary" disabled={busy} onClick={close}>
          {uiText("取消")}
        </button>
        <button
          className="primary"
          disabled={
            busy ||
            !images.length ||
            !text.trim() ||
            !accountId ||
            !authorized ||
            invalidXhs
          }
          onClick={() =>
            void action(async () => {
              await request("image_posts.create", {
                paths: images.map((image) => image.path),
                text,
                account_id: accountId,
                mode,
                operation_id: op.current,
                ...(syncXiaohongshu
                  ? {
                      sync_xiaohongshu: true,
                      xiaohongshu_account_id: xiaohongshu!.account!.account_id,
                      xiaohongshu_binding_revision:
                        xiaohongshu!.account!.binding_revision,
                      xiaohongshu_title: xhsTitle,
                    }
                  : {}),
              });
              await done();
            }, "图文任务已加入队列。")
          }
        >
          {busy ? (
            <Loader2 className="spin" size={16} />
          ) : (
            <ImagePlus size={16} />
          )}
          {uiText("加入图文队列")}
        </button>
      </div>
    </>
  );
}
