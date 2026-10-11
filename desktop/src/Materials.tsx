import { useCallback, useEffect, useState } from "react";
import {
  FolderOpen,
  Plus,
  RefreshCw,
  Play,
  Pencil,
  Trash2,
} from "lucide-react";
import { request } from "./bridge";
import { bytes } from "./types";
import { StyledSelect } from "./StyledSelect";
import { uiText } from "./i18n";

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
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const load = useCallback(async () => {
    const result = await request("materials.libraries");
    setLibraries(result.items);
  }, []);
  useEffect(() => {
    void action(load);
  }, [load]);
  return (
    <div className="library-picker">
      <label className="field">
        素材库
        <StyledSelect
          label="素材库"
          value={value}
          onChange={onChange}
          options={[
            { value: "", label: "请选择素材库" },
            ...libraries.map((l) => ({ value: l.id, label: l.name })),
          ]}
        />
      </label>
      <button className="secondary" onClick={() => setCreating(!creating)}>
        <Plus size={15} />
        新建素材库
      </button>
      {creating && (
        <form
          className="library-create"
          onSubmit={(e) => {
            e.preventDefault();
            void action(async () => {
              const result = await request("materials.create_library", {
                name,
              });
              await load();
              onChange(result.id);
              setName("");
              setCreating(false);
            }, "素材库已创建。");
          }}
        >
          <input
            autoFocus
            aria-label="素材库名称"
            placeholder="素材库名称"
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button className="primary" disabled={!name.trim()}>
            创建
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
  const [library, setLibrary] = useState("");
  const [items, setItems] = useState<Material[]>([]);
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [loaded, setLoaded] = useState("");
  const [editing, setEditing] = useState<Material | null>(null);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState<Material | null>(null);
  const [offset, setOffset] = useState(0);
  const load = useCallback(async () => {
    if (!library) {
      setItems([]);
      setPath("");
      return;
    }
    const result = await request("materials.list", { library_id: library });
    setItems(result.items);
    setPath(result.path);
    setLoaded(library);
  }, [library]);
  useEffect(() => {
    void action(load);
  }, [load]);
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
        <LibraryPicker value={library} onChange={setLibrary} action={action} />
        <p className="help">
          在任务中心新建任务，选择“下载素材”即可保存到素材库。同一链接可以重复下载。
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
              aria-label="搜索素材"
              placeholder="搜索素材"
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
              刷新
            </button>
            <button
              className="secondary"
              onClick={() =>
                action(() => request("materials.open", { library_id: library }))
              }
            >
              <FolderOpen size={15} />
              打开文件夹
            </button>
          </div>
          {loaded !== library ? (
            <p role="status">正在加载素材…</p>
          ) : (
            <>
              {!visible.length && (
                <div className="empty-state">
                  <FolderOpen size={36} />
                  <h3>暂无素材</h3>
                  <p>在任务中心下载素材后，点击刷新查看。</p>
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
                      · {bytes(item.size)}
                    </div>
                    <strong data-no-localize title={item.name}>
                      {item.name}
                    </strong>
                    <small data-no-localize>
                      {new Date(item.modified * 1000).toLocaleString()}
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
                        预览
                      </button>
                      <button
                        className="icon-button"
                        aria-label="重命名素材"
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
                        aria-label="删除素材"
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
                    上一页
                  </button>
                  <span>
                    {Math.floor(offset / 20) + 1} /{" "}
                    {Math.ceil(visible.length / 20)}
                  </span>
                  <button
                    disabled={offset + 20 >= visible.length}
                    onClick={() => setOffset(offset + 20)}
                  >
                    下一页
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
          aria-label="重命名素材"
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
            <h3>重命名素材</h3>
            <input
              autoFocus
              aria-label="文件名"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <p className="help">请保留原文件扩展名。</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setEditing(null)}>
                取消
              </button>
              <button className="primary" disabled={busy || !name.trim()}>
                保存
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
          aria-label="删除素材"
        >
          <div className="section-card section-body">
            <h3>删除素材</h3>
            <p data-no-localize>{deleting.name}</p>
            <p>只删除此素材文件，任务记录会保留。此操作无法撤销。</p>
            <div className="modal-actions">
              <button onClick={() => setDeleting(null)}>取消</button>
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
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
