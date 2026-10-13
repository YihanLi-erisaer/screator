import { useCallback, useEffect, useState } from "react";
import {
  FolderOpen,
  Plus,
  RefreshCw,
  Play,
  Pencil,
  Trash2,
  Upload,
} from "lucide-react";
import { chooseDirectory, chooseFiles, request } from "./bridge";
import { bytes } from "./types";
import { StyledSelect } from "./StyledSelect";
import { uiText, uiNode, formatDateTime } from "./i18n";
import { useToast } from "./Toast";

const materialExtensions = [
  "mp4",
  "mkv",
  "webm",
  "mov",
  "avi",
  "flv",
  "m4v",
  "ts",
  "mp3",
  "m4a",
  "aac",
  "wav",
  "flac",
  "ogg",
  "opus",
  "weba",
  "jpg",
  "jpeg",
  "png",
  "webp",
  "gif",
  "avif",
  "bmp",
  "tiff",
];
const selectedLibraryKey = "screator.materials.selectedLibrary";

function savedLibrary() {
  try {
    return localStorage.getItem(selectedLibraryKey) ?? "";
  } catch {
    return "";
  }
}

export type Library = { id: string; name: string };
type Material = { name: string; kind: string; size: number; modified: number };
export type MaterialAction = (
  fn: () => Promise<any>,
  success?: string,
) => Promise<any>;

export function LibraryPicker({
  value,
  onChange,
  action,
}: {
  value: string;
  onChange: (id: string) => void;
  action: MaterialAction;
}) {
  const { showError } = useToast();
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [creating, setCreating] = useState(false);
  const [directory, setDirectory] = useState("");
  const [saving, setSaving] = useState(false);
  const load = useCallback(async (isCurrent: () => boolean = () => true) => {
    const result = await request("materials.libraries");
    if (isCurrent()) setLibraries(result.items);
  }, []);
  useEffect(() => {
    let current = true;
    // Reading libraries must not block closing the dialog or another picker.
    void load(() => current).catch((error) => {
      if (current)
        showError(String(error instanceof Error ? error.message : error));
    });
    return () => {
      current = false;
    };
  }, [load, showError]);
  return (
    <div className="library-picker">
      <label className="field">
        {uiText("素材库")}
        <StyledSelect
          label="素材库"
          value={value}
          onChange={onChange}
          options={[
            { value: "", label: "请选择素材库" },
            ...libraries.map((l) => ({
              value: l.id,
              label: l.name,
              localize: false,
            })),
          ]}
        />
      </label>
      <button className="secondary" onClick={() => setCreating(!creating)}>
        <Plus size={15} />
        {uiText("新建素材库")}
      </button>
      {creating && (
        <form
          className="library-create"
          onSubmit={(e) => {
            e.preventDefault();
            if (saving || !directory.trim()) return;
            setSaving(true);
            void action(async () => {
              const result = await request("materials.create_library", {
                directory,
              });
              await load();
              onChange(result.id);
              setDirectory("");
              setCreating(false);
            }, "素材库已创建。").finally(() => setSaving(false));
          }}
        >
          <label className="field library-location">
            {uiText("素材库文件夹")}
            <div className="inline-controls">
              <input
                aria-label={uiText("素材库文件夹")}
                placeholder={uiText("请选择素材库文件夹")}
                value={directory}
                readOnly
              />
              <button
                type="button"
                className="secondary"
                disabled={saving}
                onClick={() =>
                  action(async () => {
                    const selected = await chooseDirectory();
                    if (selected) setDirectory(selected);
                  })
                }
              >
                <FolderOpen size={15} />
                {uiText("选择文件夹")}
              </button>
            </div>
          </label>
          <p className="help">
            {uiText("所选文件夹将作为素材库，素材库名称自动使用文件夹名称。")}
          </p>
          <button className="primary" disabled={saving || !directory.trim()}>
            {uiText("创建")}
          </button>
        </form>
      )}
    </div>
  );
}

export function Materials({
  action,
  busy,
}: {
  action: MaterialAction;
  busy: boolean;
}) {
  const { showSuccess, showError } = useToast();
  const [library, setLibrary] = useState(savedLibrary);
  const selectLibrary = useCallback((id: string) => {
    setLibrary(id);
    try {
      if (id) localStorage.setItem(selectedLibraryKey, id);
      else localStorage.removeItem(selectedLibraryKey);
    } catch {
      /* The selection remains available for the current page. */
    }
  }, []);
  const [items, setItems] = useState<Material[]>([]);
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [loaded, setLoaded] = useState("");
  const [editing, setEditing] = useState<Material | null>(null);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState<Material | null>(null);
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    async (isCurrent: () => boolean = () => true) => {
      if (!library) {
        setItems([]);
        setPath("");
        setLoaded("");
        return;
      }
      const libraries = await request("materials.libraries");
      if (!isCurrent()) return;
      if (!libraries.items.some((item: Library) => item.id === library)) {
        selectLibrary("");
        return;
      }
      const result = await request("materials.list", { library_id: library });
      if (!isCurrent()) return;
      setItems(result.items);
      setPath(result.path);
      setLoaded(library);
    },
    [library, selectLibrary],
  );
  useEffect(() => {
    let current = true;
    // Read independently of the picker load so restoring a library cannot be
    // skipped by the application's single mutation/busy guard.
    void load(() => current).catch((error) => {
      if (current)
        showError(String(error instanceof Error ? error.message : error));
    });
    return () => {
      current = false;
    };
  }, [load, showError]);
  useEffect(() => {
    setOffset(0);
    setEditing(null);
    setDeleting(null);
  }, [library, search, filter]);
  const visible = items.filter(
    (item) =>
      item.name.toLowerCase().includes(search.toLowerCase()) &&
      (!filter || item.kind === filter),
  );
  return (
    <section className="materials-page">
      <div className="section-card section-body">
        <LibraryPicker
          value={library}
          onChange={selectLibrary}
          action={action}
        />
        <p className="help">
          {uiText(
            "点击右上角“新建下载任务”下载素材到素材库。同一链接可以重复下载。",
          )}
        </p>
        {path && (
          <p className="help material-path" data-no-localize>
            {path}
          </p>
        )}
      </div>
      {library && (
        <>
          <div className="material-toolbar">
            <input
              aria-label={uiText("搜索素材")}
              placeholder={uiText("搜索素材")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <StyledSelect
              label="素材类型"
              value={filter}
              onChange={setFilter}
              options={[
                { value: "", label: "全部素材" },
                { value: "video", label: "视频" },
                { value: "image", label: "图片" },
                { value: "audio", label: "音频" },
              ]}
            />
            <button
              className="secondary"
              disabled={busy}
              onClick={() => action(load)}
            >
              <RefreshCw size={15} />
              {uiText("刷新")}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const paths = await chooseFiles(materialExtensions);
                  if (!paths?.length) return;
                  await request("materials.import", {
                    library_id: library,
                    paths,
                  });
                  await load();
                  setSearch("");
                  setFilter("");
                  setOffset(0);
                  showSuccess("本地素材导入完成。");
                })
              }
            >
              <Upload size={15} />
              {uiText("添加本地素材")}
            </button>
            <button
              className="secondary"
              onClick={() =>
                action(() => request("materials.open", { library_id: library }))
              }
            >
              <FolderOpen size={15} />
              {uiText("打开文件夹")}
            </button>
          </div>
          {loaded !== library ? (
            <p role="status">{uiText("正在加载素材…")}</p>
          ) : (
            <>
              {!visible.length && (
                <div className="empty-state">
                  <FolderOpen size={36} />
                  <h3>{uiText("暂无素材")}</h3>
                  <p>
                    {uiText(
                      "添加本地素材，或点击右上角“新建下载任务”下载素材后点击刷新查看。",
                    )}
                  </p>
                </div>
              )}
              <div className="material-grid">
                {visible.slice(offset, offset + 20).map((item) => (
                  <article
                    className="section-card material-card"
                    key={item.name}
                  >
                    <div className="material-kind">
                      {uiText(
                        (
                          {
                            video: "视频",
                            image: "图片",
                            audio: "音频",
                          } as Record<string, string>
                        )[item.kind],
                      )}{" "}
                      · {uiNode(bytes(item.size))}
                    </div>
                    <strong data-no-localize title={item.name}>
                      {item.name}
                    </strong>
                    <small data-no-localize>
                      {formatDateTime(item.modified * 1000)}
                    </small>
                    <div className="inline-controls">
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() =>
                          action(() =>
                            request("materials.open", {
                              library_id: library,
                              name: item.name,
                            }),
                          )
                        }
                      >
                        <Play size={14} />
                        {uiText("预览")}
                      </button>
                      <button
                        className="icon-button"
                        aria-label={uiText("重命名素材")}
                        disabled={busy}
                        onClick={() => {
                          setEditing(item);
                          setName(item.name);
                        }}
                      >
                        <Pencil size={15} />
                      </button>
                      <button
                        className="icon-button danger"
                        aria-label={uiText("删除素材")}
                        disabled={busy}
                        onClick={() => setDeleting(item)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              {visible.length > 20 && (
                <div className="inline-controls">
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 20))}
                  >
                    {uiText("上一页")}
                  </button>
                  <span>
                    {Math.floor(offset / 20) + 1} /{" "}
                    {Math.ceil(visible.length / 20)}
                  </span>
                  <button
                    disabled={offset + 20 >= visible.length}
                    onClick={() => setOffset(offset + 20)}
                  >
                    {uiText("下一页")}
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
      {editing && (
        <div
          className="material-dialog"
          role="dialog"
          aria-modal="true"
          aria-label={uiText("重命名素材")}
        >
          <form
            className="section-card section-body"
            onSubmit={(e) => {
              e.preventDefault();
              void action(async () => {
                await request("materials.rename", {
                  library_id: library,
                  name: editing.name,
                  new_name: name,
                });
                setEditing(null);
                await load();
              }, "素材已重命名。");
            }}
          >
            <h3>{uiText("重命名素材")}</h3>
            <input
              autoFocus
              aria-label={uiText("文件名")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <p className="help">{uiText("请保留原文件扩展名。")}</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setEditing(null)}>
                {uiText("取消")}
              </button>
              <button className="primary" disabled={busy || !name.trim()}>
                {uiText("保存")}
              </button>
            </div>
          </form>
        </div>
      )}
      {deleting && (
        <div
          className="material-dialog"
          role="dialog"
          aria-modal="true"
          aria-label={uiText("删除素材")}
        >
          <div className="section-card section-body">
            <h3>{uiText("删除素材")}</h3>
            <p data-no-localize>{deleting.name}</p>
            <p>
              {uiText("只删除此素材文件，任务记录会保留。此操作无法撤销。")}
            </p>
            <div className="modal-actions">
              <button onClick={() => setDeleting(null)}>
                {uiText("取消")}
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    await request("materials.delete", {
                      library_id: library,
                      name: deleting.name,
                      confirmed: true,
                    });
                    setDeleting(null);
                    await load();
                  }, "素材已删除，任务记录已保留。")
                }
              >
                {uiText("确认删除")}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
