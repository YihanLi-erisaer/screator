import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  lazy,
  Suspense,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  FolderOpen,
  History,
  ImagePlus,
  ChartNoAxesCombined,
  KeyRound,
  Link2,
  ListVideo,
  Loader2,
  Monitor,
  Moon,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sun,
  Trash2,
  Upload,
  UserRound,
  Video,
  X,
  Zap,
} from "lucide-react";
import {
  request,
  beginShutdown,
  subscribe,
  preview,
  chooseFile,
  chooseDirectory,
  saveHistory,
  external,
  operationId,
  onClose,
  closeApp,
  forceCloseApp,
  setWindowTheme,
  setWindowIcon,
} from "./bridge";
import {
  active,
  bytes,
  editable,
  labels,
  platformOptions,
  retryable,
  transferRate,
  type Config,
  type Progress,
  type Task,
} from "./types";
import { AccountsPanel, AccountSelector, QueueOverview } from "./Accounts";
import { DouyinAccountPanel, PublicationDetails } from "./Douyin";
import { AcfunAccountPanel, AcfunChannelSelect } from "./Acfun";
import {
  YoutubeAccountPanel,
  YoutubeCategorySelect,
  YoutubePrivacySelect,
} from "./Youtube";
import {
  SocialAccountPanels,
  SocialTargets,
  type SocialTarget,
} from "./Social";
import { accountLabel, canConfirmPublication, type BiliAccount } from "./types";
import { StyledSelect } from "./StyledSelect";
import { StatusBadge } from "./StatusBadge";
import { SubmissionTrend, type SubmissionTrendData } from "./SubmissionTrend";
import { ReportError, useToast } from "./Toast";
import {
  setUiLanguage,
  uiText,
  uiNode,
  useUiLanguage,
  getUiLanguage,
} from "./i18n";
import { Materials, LibraryPicker } from "./Materials";
import { ImagePostForm, PostImages, type PostImage } from "./ImagePost";
import { XiaohongshuAccountPanel } from "./Xiaohongshu";

const SetupWizard = lazy(() => import("./SetupWizard"));
const TranslationPanel = lazy(() => import("./TranslationPanel"));
const DataCenter = lazy(() =>
  import("./DataCenter").then((module) => ({ default: module.DataCenter })),
);
const ResourceMonitor = lazy(() =>
  import("./ResourceMonitor").then((module) => ({
    default: module.ResourceMonitor,
  })),
);

type Page = "tasks" | "materials" | "history" | "data" | "account" | "settings";
const PAGE_SIZE = 20;
type ShutdownStatus = {
  closing?: boolean;
  ready: boolean;
  pending_task_ids: string[];
  inflight_task_ids: string[];
  stages?: string[];
  translation_active?: boolean;
};
const titles = {
  tasks: "投稿中心",
  materials: "素材管理",
  history: "投稿记录",
  data: "数据中心",
  account: "账号与连接",
  settings: "设置",
};
const progressSpeed = (progress?: Progress) => {
  if (progress?.speed != null) return transferRate(progress.speed);
  if (progress?.speed_ratio != null)
    return `${progress.speed_ratio.toFixed(2)}×`;
  return "";
};

function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={wide ? "modal wide" : "modal"}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        close();
      }}
    >
      <div className="modal-head">
        <h2 id={titleId}>{uiNode(title)}</h2>
        <button
          className="icon-button"
          aria-label={uiText("关闭对话框")}
          onClick={close}
        >
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

function Status({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <span />
      {uiNode(labels[status] || status)}
    </span>
  );
}

function ResizingList({ children }: { children: ReactNode }) {
  const content = useRef<HTMLDivElement>(null);
  const frame = useRef<number | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const scheduleHeight = useCallback(() => {
    if (!content.current) return;
    const next = content.current.getBoundingClientRect().height;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      setHeight((current) =>
        current === null || Math.abs(current - next) > 1 ? next : current,
      );
      frame.current = null;
    });
  }, []);
  useLayoutEffect(() => {
    if (height === null && content.current) {
      setHeight(content.current.getBoundingClientRect().height);
    } else {
      scheduleHeight();
    }
  });
  useEffect(() => {
    if (!content.current) return;
    const observer = new ResizeObserver(scheduleHeight);
    observer.observe(content.current);
    return () => {
      observer.disconnect();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [scheduleHeight]);
  return (
    <div className="list-height-slot" style={{ height: height ?? undefined }}>
      <div ref={content}>{children}</div>
    </div>
  );
}

export default function App() {
  useUiLanguage();
  const { showError, showSuccess } = useToast();
  const [page, setPage] = useState<Page>("tasks");
  const [config, setConfig] = useState<Config | null>(null);
  const configRequest = useRef(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loadedListKey, setLoadedListKey] = useState("");
  const [total, setTotal] = useState(0);
  const [allTotal, setAllTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [todaySubmittedByAccount, setTodaySubmittedByAccount] = useState<
    Record<string, number>
  >({});
  const [submissionTrend, setSubmissionTrend] =
    useState<SubmissionTrendData | null>(null);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [connected, setConnected] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [newTask, setNewTask] = useState<"download" | "publish" | null>(null);
  const [confirmDeleteAllHistory, setConfirmDeleteAllHistory] = useState(false);
  const [setup, setSetup] = useState(false);
  const [selected, setSelected] = useState<Task | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [auth, setAuth] = useState<any>({
    configured: false,
    login: { status: "idle" },
  });
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [closing, setClosing] = useState(false);
  const [queue, setQueue] = useState<any>(null);
  const [taskPlatform, setTaskPlatform] = useState("");
  const [historyPlatform, setHistoryPlatform] = useState("");
  const [accountOptions, setAccountOptions] = useState<BiliAccount[]>([]);
  const [shutdownStarted, setShutdownStarted] = useState(false);
  const [shutdownStatus, setShutdownStatus] = useState<ShutdownStatus | null>(
    null,
  );
  const shutdownRef = useRef(false);
  const finishingClose = useRef(false);
  const [forceClosing, setForceClosing] = useState(false);
  const taskRequest = useRef(0);
  const listPlatform = page === "history" ? historyPlatform : taskPlatform;
  const listKey = JSON.stringify([page, listPlatform, query, filter, offset]);
  const taskRowsByKey = useRef(new Map<string, number>());
  const listLoading =
    (page === "tasks" || page === "history") && loadedListKey !== listKey;
  const historyLoading = page === "history" && listLoading;
  const taskOverviewLoading =
    page === "tasks" && !loadedListKey.startsWith('["tasks",');
  const latestTasks = useRef<Record<string, Task>>({});
  const [queueActive, setQueueActive] = useState<any[]>([]);
  const action = useCallback(
    async (work: () => Promise<unknown>, success?: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      try {
        await work();
        if (success) showSuccess(success);
      } catch (error) {
        showError(String(error instanceof Error ? error.message : error));
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [showError, showSuccess],
  );
  const loadConfig = useCallback(async (checkServices = true) => {
    const generation = ++configRequest.current;
    const value: Config = await request("settings.get", {
      check_services: checkServices,
    });
    if (generation !== configRequest.current) return;
    setUiLanguage(value.ui_language ?? "zh-CN");
    setConfig(value);
    if (!checkServices) {
      void request<Partial<Config>>("settings.status")
        .then((status) => {
          if (generation === configRequest.current)
            setConfig((current) => current && { ...current, ...status });
        })
        .catch(() => {
          // Leave readiness pending; opening settings can explicitly retry.
        });
    }
  }, []);
  const loadTasks = useCallback(async () => {
    const generation = ++taskRequest.current;
    const result = await request("tasks.list", {
      platform: listPlatform,
      search: query,
      status: filter,
      offset,
      limit: PAGE_SIZE,
      history: page === "history",
    });
    if (generation !== taskRequest.current) return;
    result.items = result.items.map((t: Task) => {
      const old = latestTasks.current[t.task_id];
      const value = old && old.revision > t.revision ? { ...t, ...old } : t;
      latestTasks.current[t.task_id] = value;
      return value;
    });
    setTasks(result.items);
    if (page === "tasks")
      taskRowsByKey.current.set(listKey, result.items.length);
    setQueue((old: any) =>
      !old || (result.queue.queue_revision || 0) >= (old.queue_revision || 0)
        ? result.queue
        : old,
    );
    setTotal(result.total);
    setAllTotal(result.all_total ?? result.total);
    setCounts(result.counts);
    setTodaySubmittedByAccount(result.today_submitted_by_account || {});
    if (page === "history") setSubmissionTrend(result.submission_trend || null);
    setQueueActive(result.queue.active);
    setLoadedListKey(listKey);
    setSelected((old) =>
      old
        ? {
            ...old,
            ...result.items.find((t: Task) => t.task_id === old.task_id),
          }
        : null,
    );
  }, [query, filter, offset, page, listPlatform, listKey]);
  const refreshAccount = useCallback(async () => {
    const [auth, accounts] = await Promise.all([
      request("auth.status"),
      request("accounts.list"),
    ]);
    setAuth(auth);
    setAccountOptions(accounts.items);
  }, []);

  useEffect(() => {
    let alive = true;
    request("system.health")
      .then(async (result) => {
        if (result.protocol_version !== 2)
          throw new Error("桌面与后台协议版本不匹配。");
        if (!alive) return;
        if (alive) {
          setConnected(true);
          setConnectionError("");
        }
        await Promise.all([loadConfig(false), refreshAccount()]);
        request("system.diagnostics")
          .then((value) => {
            if (alive) setDiagnostics(value);
          })
          .catch(() => {});
      })
      .catch((error) => {
        if (alive) setConnectionError(String(error));
      });
    let stop = () => {};
    let stopClose = () => {};
    subscribe((event) => {
      if (!alive) return;
      if (event.event === "system.shutdown_status") {
        if (shutdownRef.current) setShutdownStatus(event.payload);
        return;
      }
      if (shutdownRef.current) return;
      if (event.event === "accounts.changed") void refreshAccount();
      if (event.event === "queue.changed") {
        setQueue((old: any) =>
          !old ||
          (event.payload.queue_revision || 0) >= (old.queue_revision || 0)
            ? event.payload
            : old,
        );
        setQueueActive(event.payload.active || []);
      }
      if (event.event === "publication.changed") {
        void request<Task>("tasks.get", { task_id: event.payload.task_id })
          .then((next) => {
            setSelected((old) => (old?.task_id === next.task_id ? next : old));
            setTasks((old) =>
              old.map((t) => (t.task_id === next.task_id ? next : t)),
            );
          })
          .catch(() => {});
      }
      if (event.event === "task.status") {
        const before = latestTasks.current[event.payload.task_id];
        if (before && before.revision > event.payload.revision) return;
        latestTasks.current[event.payload.task_id] = {
          ...before,
          ...event.payload,
        };
        setTasks((old) =>
          old.map((task) =>
            task.task_id === event.payload.task_id &&
            event.payload.revision >= task.revision
              ? { ...task, ...event.payload }
              : task,
          ),
        );
        setSelected((old) =>
          old &&
          old.task_id === event.payload.task_id &&
          event.payload.revision >= old.revision
            ? { ...old, ...event.payload }
            : old,
        );
        setProgress((old) => {
          const next = { ...old };
          delete next[event.payload.task_id];
          return next;
        });
      }
      if (
        event.event === "task.progress" &&
        (!latestTasks.current[event.payload.task_id]?.run_id ||
          latestTasks.current[event.payload.task_id]?.run_id ===
            event.payload.run_id)
      )
        setProgress((old) => ({
          ...old,
          [event.payload.task_id]: event.payload,
          ...(event.payload.publication_id
            ? { [event.payload.publication_id]: event.payload }
            : {}),
        }));
      if (event.event === "auth.status") {
        setAuth((old: any) => ({
          ...old,
          login: { ...old.login, ...event.payload },
        }));
        if (event.payload.status === "success") {
          void refreshAccount();
          showSuccess("B 站登录成功，凭据已保存在本机。");
        }
      }
      if (event.event === "task.repaired")
        showSuccess("替换素材已准备好，请在创作中心替换原稿件的视频。");
      if (event.event === "disconnected") {
        setConnected(false);
        setConnectionError(
          "后台连接已断开。请重新启动应用；未完成任务将在启动后恢复。",
        );
      }
    }).then((fn) => {
      if (alive) stop = fn;
      else fn();
    });
    onClose(() => setClosing(true)).then((fn) => {
      if (alive) stopClose = fn;
      else fn();
    });
    return () => {
      alive = false;
      configRequest.current += 1;
      stop();
      stopClose();
    };
  }, [loadConfig, refreshAccount, showSuccess]);

  useEffect(() => {
    if (!connected || page === "data" || shutdownStarted) return;
    let disposed = false;
    let loading = false;
    const update = async () => {
      if (loading || disposed) return;
      loading = true;
      try {
        await loadTasks();
      } catch {
        /* connection lifecycle reports the failure */
      } finally {
        loading = false;
      }
    };
    void update();
    const interval = setInterval(update, 1500);
    return () => {
      disposed = true;
      clearInterval(interval);
    };
  }, [connected, loadTasks, page, shutdownStarted]);

  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    void setWindowTheme(config?.theme ?? "system").catch((error) =>
      console.error("Failed to update window theme", error),
    );
    const apply = () => {
      const theme =
        config?.theme === "system" || !config
          ? media.matches
            ? "dark"
            : "light"
          : config.theme;
      document.documentElement.dataset.theme = theme;
      document
        .querySelector<HTMLLinkElement>('link[rel="icon"]')
        ?.setAttribute(
          "href",
          theme === "dark" ? "/brand-dark.png" : "/brand.png",
        );
      void setWindowIcon(theme).catch((error) =>
        console.error("Failed to update window icon", error),
      );
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [config?.theme]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        if (connected)
          setNewTask(page === "materials" ? "download" : "publish");
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, [connected, page]);

  useEffect(() => {
    if (!shutdownStarted) return;
    let stop = false;
    const tick = async () => {
      try {
        if (!finishingClose.current) {
          const state = (await request(
            "system.shutdown_status",
          )) as ShutdownStatus;
          if (!stop) setShutdownStatus(state);
        }
      } catch (e) {
        showError(String(e));
      }
      if (!stop) timer = setTimeout(tick, 2000);
    };
    let timer = setTimeout(tick, 0);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [shutdownStarted, showError]);
  useEffect(() => {
    if (!shutdownStarted || !shutdownStatus?.ready || finishingClose.current)
      return;
    finishingClose.current = true;
    void closeApp().catch((error) => {
      finishingClose.current = false;
      showError(String(error));
    });
  }, [shutdownStarted, shutdownStatus, showError]);
  useEffect(() => {
    if (connectionError)
      showError(uiText("未连接到后台：{p0}", { p0: connectionError }), {
        label: "重新连接",
        onClick: () => location.reload(),
      });
  }, [connectionError, showError]);

  const navigate = (next: Page) => {
    setPage(next);
    if (next !== page) setLoadedListKey("");
    setSelected(null);
    setOffset(0);
    setFilter("");
    setQuery("");
  };
  const theme = async () => {
    const next =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    setConfig(await request("settings.update", { values: { theme: next } }));
  };
  const deleteHistory = (task: Task) =>
    action(async () => {
      await request("history.delete", {
        task_id: task.task_id,
        expected_revision: task.revision,
        confirmed: true,
      });
      delete latestTasks.current[task.task_id];
      await loadTasks();
      setSelected(null);
    }, "本机记录已删除。");
  const activeAccounts = accountOptions.filter(
    (account) => account.lifecycle === "active",
  );
  const nav = [
    { id: "tasks", icon: ListVideo },
    { id: "materials", icon: FolderOpen },
    { id: "history", icon: History },
    { id: "data", icon: ChartNoAxesCombined },
    { id: "account", icon: KeyRound },
    { id: "settings", icon: Settings2 },
  ] as const;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("tasks");
          }}
        >
          <img className="brand-icon-light" src="/brand.png" alt="" />
          <img className="brand-icon-dark" src="/brand-dark.png" alt="" />
          <span>
            screator<small>by StarDazz</small>
          </span>
        </a>
        <div className="nav-label">{uiText("工作空间")}</div>
        <nav aria-label={uiText("主导航")}>
          {nav.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "selected" : ""}`}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={18} />
              {uiNode(titles[item.id])}
              {item.id === "tasks" && !!counts.ready && (
                <span className="nav-count">{counts.ready}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-card">
            <ShieldCheck size={18} />
            <div>
              {uiText("在你的设备上运行")}
              <small>{uiText("素材处理在本机 · 按平台授权投稿")}</small>
            </div>
          </div>
          <div className="sidebar-footer">
            <button
              className="text-button studio"
              onClick={() => external("https://stardazz-com.vercel.app/")}
            >
              StarDazz <ExternalLink size={12} />
            </button>
            <button
              className="icon-button"
              title={uiText("切换明暗主题")}
              aria-label={uiText("切换明暗主题")}
              onClick={() => action(theme)}
              disabled={busy || !config}
            >
              <Sun size={16} />
            </button>
          </div>
          <span className="version">DESKTOP · v1.0.0alpha</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            {uiText("工作空间 ")}
            <span>/</span> <strong>{uiNode(titles[page])}</strong>
          </div>
          <div className="connection">
            <i className={connected ? "online" : ""} />
            {uiNode(
              preview
                ? "界面预览 · 示例数据"
                : connected
                  ? "本地服务已连接"
                  : "正在连接本地服务",
            )}
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {uiNode(
                  page === "tasks"
                    ? "本地创作流程"
                    : page === "materials"
                      ? "你的素材库"
                      : page === "history"
                        ? "历史投稿记录"
                        : page === "data"
                          ? "创作数据分析"
                          : page === "account"
                            ? "已连接服务"
                            : "个性化设置",
                )}
              </span>
              <h1>{uiNode(titles[page])}</h1>
              <p>
                {uiNode(
                  page === "tasks"
                    ? "从链接到投稿，每一步都清晰可见。"
                    : page === "materials"
                      ? "素材库 · 视频 / 图片 / 音频"
                      : page === "history"
                        ? "所有已提交稿件，以及需要你核对的结果。"
                        : page === "data"
                          ? "查看账号和稿件表现，以及平台审核结果。"
                          : page === "account"
                            ? "连接你的账号，开始本地创作流程。"
                            : "按你的习惯设置工作目录与投稿默认值。",
                )}
              </p>
            </div>
            {(page === "tasks" || page === "materials") && (
              <button
                className="primary"
                onClick={() =>
                  setNewTask(page === "materials" ? "download" : "publish")
                }
                disabled={!connected}
              >
                <Plus size={17} />
                {uiText(page === "materials" ? "新建下载任务 " : "新建投稿任务 ")}
                <kbd>⌘ / Ctrl N</kbd>
              </button>
            )}
            {page === "history" && (
              <div className="button-row">
                <button
                  className="secondary"
                  disabled={!connected || preview || busy}
                  onClick={() =>
                    void action(async () => {
                      const path = await chooseFile(["json"]);
                      if (!path) return;
                      const result = await request("history.import", { path });
                      await loadTasks();
                      setFilter("");
                      setQuery("");
                      setOffset(0);
                      showSuccess(
                        uiText("导入 {p0} 条记录，跳过 {p1} 条已有记录。", {
                          p0: result.imported,
                          p1: result.skipped,
                        }),
                      );
                    })
                  }
                >
                  <Upload size={16} />
                  {uiText(" 导入投稿记录")}
                </button>
                <button
                  className="secondary"
                  disabled={!connected || preview || busy}
                  onClick={() =>
                    void action(async () => {
                      const path = await saveHistory();
                      if (!path) return;
                      const result = await request("history.export", { path });
                      showSuccess(
                        uiText("已导出 {p0} 条投稿记录。", {
                          p0: result.exported,
                        }),
                      );
                    })
                  }
                >
                  <ArrowDownToLine size={16} />
                  {uiText(" 导出投稿记录")}
                </button>
                <button
                  className="secondary danger"
                  disabled={
                    !connected || busy || historyLoading || allTotal === 0
                  }
                  onClick={() => setConfirmDeleteAllHistory(true)}
                >
                  <Trash2 size={16} />
                  {uiText(" 删除所有投稿记录")}
                </button>
              </div>
            )}
          </div>
          <Suspense
            fallback={
              <div className="help" role="status">
                {uiText("加载中…")}
              </div>
            }
          >
            {page === "materials" && <Materials action={action} busy={busy} />}
            {page === "data" && (
              <DataCenter
                paused={shutdownStarted}
                accounts={accountOptions}
                onOpenTask={(taskId) => {
                  void action(async () =>
                    setSelected(
                      await request("tasks.get", { task_id: taskId }),
                    ),
                  );
                }}
              />
            )}
            {(page === "tasks" || page === "history") && (
              <>
                {page === "tasks" && (
                  <div>
                    <div className="stats-heading">
                      {uiText("B 站账号 · 今日提交成功")}
                    </div>
                    <div
                      className="stats-strip"
                      style={{
                        gridTemplateColumns: `repeat(${Math.max(activeAccounts.length, 1)}, minmax(0, 1fr))`,
                      }}
                    >
                      {taskOverviewLoading
                        ? Array.from(
                            { length: Math.max(activeAccounts.length, 1) },
                            (_, index) => (
                              <div
                                className="stat stat-skeleton"
                                key={index}
                                aria-hidden="true"
                              >
                                <span className="stat-icon skeleton-block" />
                                <div>
                                  <span className="skeleton-block skeleton-stat-label" />
                                  <strong className="skeleton-block skeleton-stat-value" />
                                </div>
                              </div>
                            ),
                          )
                        : activeAccounts.length === 0 && (
                            <div className="stat stat-empty">
                              {uiText(
                                "连接 B 站账号后，这里会显示各账号今日提交成功数。",
                              )}
                            </div>
                          )}
                      {!taskOverviewLoading &&
                        activeAccounts.map((account) => (
                          <div
                            className="stat"
                            key={account.account_id}
                            title={accountLabel(account)}
                          >
                            <span className="stat-icon">
                              <UserRound size={18} />
                            </span>
                            <div>
                              <span>
                                {account.remark ||
                                  account.nickname ||
                                  `UID ${account.uid}`}
                              </span>
                              <strong>
                                {uiNode(
                                  String(
                                    todaySubmittedByAccount[
                                      account.account_id
                                    ] || 0,
                                  ).padStart(2, "0"),
                                )}
                              </strong>
                            </div>
                          </div>
                        ))}
                    </div>
                  </div>
                )}
                {config &&
                  !config.readiness_pending &&
                  !config.translation_ready &&
                  page === "tasks" && (
                    <div className="setup-banner">
                      <div className="setup-icon">
                        <Zap size={20} />
                      </div>
                      <div>
                        <strong>{uiText("先完成一次简单的配置")}</strong>
                        <p>
                          {uiText(
                            "配置本地翻译或 DeepL 并连接 B 站账号，之后就可以在这里管理任务。",
                          )}
                        </p>
                      </div>
                      <button
                        className="secondary"
                        onClick={() => setSetup(true)}
                      >
                        {uiText("开始配置 ")}
                        <ArrowRight size={15} />
                      </button>
                    </div>
                  )}
                {page === "history" ? (
                  historyLoading ? (
                    <section
                      className="submission-trend history-trend-skeleton"
                      aria-label={uiText("正在加载投稿记录")}
                      role="status"
                    >
                      <div
                        className="submission-trend-heading"
                        aria-hidden="true"
                      >
                        <div className="submission-trend-identity">
                          <span className="skeleton-block skeleton-trend-icon" />
                          <span className="skeleton-trend-text">
                            <span className="skeleton-block skeleton-trend-heading" />
                            <span className="skeleton-block skeleton-trend-caption" />
                          </span>
                        </div>
                        <span className="skeleton-block skeleton-trend-total" />
                      </div>
                      <div className="submission-trend-body" aria-hidden="true">
                        <div className="submission-trend-chart skeleton-block skeleton-trend-chart" />
                      </div>
                      <div
                        className="submission-trend-legend"
                        aria-hidden="true"
                      >
                        {activeAccounts.map((account) => (
                          <span
                            className="skeleton-block skeleton-trend-legend"
                            key={account.account_id}
                          />
                        ))}
                      </div>
                    </section>
                  ) : (
                    <SubmissionTrend
                      trend={submissionTrend}
                      accounts={accountOptions}
                    />
                  )
                ) : taskOverviewLoading ? (
                  <div
                    className="queue-overview queue-overview-skeleton"
                    role="status"
                    aria-label={uiText("正在加载任务")}
                  >
                    {Array.from(
                      {
                        length:
                          2 + platformOptions.length + activeAccounts.length,
                      },
                      (_, index) => (
                        <div
                          className="queue-card"
                          key={index}
                          aria-hidden="true"
                        >
                          <strong className="skeleton-block skeleton-queue-title" />
                          <small className="skeleton-block skeleton-queue-detail" />
                        </div>
                      ),
                    )}
                  </div>
                ) : (
                  <QueueOverview queue={queue} accounts={auth.accounts || []} />
                )}
                <div className="task-panel">
                  <div
                    className={`panel-toolbar platform-toolbar ${page}-toolbar`}
                  >
                    <div
                      className="tabs"
                      role="tablist"
                      aria-label={uiText("投稿平台")}
                    >
                      {[
                        {
                          value: "",
                          label: page === "history" ? "全部记录" : "全部任务",
                        },
                        ...platformOptions,
                      ].map((platform) => (
                        <button
                          key={platform.value}
                          role="tab"
                          aria-selected={listPlatform === platform.value}
                          className={
                            listPlatform === platform.value ? "current" : ""
                          }
                          onClick={() => {
                            if (page === "history")
                              setHistoryPlatform(platform.value);
                            else setTaskPlatform(platform.value);
                            setOffset(0);
                          }}
                        >
                          {uiNode(platform.label)}
                          {!platform.value && (
                            <span>{uiNode(listLoading ? "…" : allTotal)}</span>
                          )}
                        </button>
                      ))}
                    </div>
                    <div className="list-search-filters">
                      <label className="search">
                        <Search size={16} />
                        <input
                          aria-label={uiText("搜索任务")}
                          placeholder={uiText("搜索标题或视频 ID")}
                          value={query}
                          onChange={(e) => {
                            setQuery(e.target.value);
                            setOffset(0);
                          }}
                        />
                      </label>
                      <StyledSelect
                        label={
                          page === "history"
                            ? "按投稿状态筛选"
                            : "按任务状态筛选"
                        }
                        className="list-status-filter"
                        value={filter}
                        onChange={(value) => {
                          setFilter(value);
                          setOffset(0);
                        }}
                        options={
                          page === "history"
                            ? [
                                { value: "", label: "全部状态" },
                                { value: "submitted", label: "已提交" },
                                {
                                  value: "submission_unknown",
                                  label: "待核对",
                                },
                                {
                                  value: "partial_success",
                                  label: "部分已提交",
                                },
                                {
                                  value: "completed_with_abandon",
                                  label: "已结束",
                                },
                              ]
                            : [
                                { value: "", label: "全部状态" },
                                ...["ready", "downloaded", "failed"].map(
                                  (status) => ({
                                    value: status,
                                    label: labels[status],
                                  }),
                                ),
                              ]
                        }
                      />
                    </div>
                  </div>
                  <div className="table-head">
                    <span>{uiText("视频 / 标题")}</span>
                    <span>{uiText("当前状态")}</span>
                    <span>{uiText("最近更新")}</span>
                    <span />
                  </div>
                  <ResizingList key={page}>
                    {listLoading &&
                    page === "tasks" &&
                    taskRowsByKey.current.get(listKey) === 0 ? (
                      <div
                        className="empty-state empty-state-skeleton"
                        role="status"
                        aria-label={uiText("正在加载任务")}
                      >
                        <span
                          className="skeleton-block skeleton-empty-art"
                          aria-hidden="true"
                        />
                        <span
                          className="skeleton-block skeleton-empty-heading"
                          aria-hidden="true"
                        />
                        <span
                          className="skeleton-block skeleton-empty-copy"
                          aria-hidden="true"
                        />
                      </div>
                    ) : listLoading ? (
                      <div
                        className={`task-rows task-rows-skeleton ${page}-rows-skeleton`}
                        role="status"
                        aria-label={uiText(
                          page === "history"
                            ? "正在加载投稿记录"
                            : "正在加载任务",
                        )}
                      >
                        {Array.from(
                          {
                            length:
                              page === "tasks"
                                ? (taskRowsByKey.current.get(listKey) ?? 3)
                                : 5,
                          },
                          (_, index) => (
                            <div
                              className="task-row"
                              key={index}
                              aria-hidden="true"
                            >
                              <span className="task-title">
                                <span className="skeleton-block skeleton-video" />
                                <span className="skeleton-title-lines">
                                  <strong className="skeleton-block skeleton-title-line" />
                                  <small className="skeleton-block skeleton-subtitle-line" />
                                </span>
                              </span>
                              <span className="skeleton-block skeleton-status" />
                              <time className="skeleton-block skeleton-time" />
                              <span />
                            </div>
                          ),
                        )}
                      </div>
                    ) : tasks.length ? (
                      <div className="task-rows">
                        {tasks.map((task) => (
                          <button
                            key={task.task_id}
                            className="task-row"
                            onClick={() =>
                              action(async () =>
                                setSelected(
                                  await request("tasks.get", {
                                    task_id: task.task_id,
                                  }),
                                ),
                              )
                            }
                          >
                            <span className="task-title">
                              <span className="video-tile">
                                {task.source_kind === "image_post" ? (
                                  <ImagePlus size={20} />
                                ) : (
                                  <Video size={20} />
                                )}
                              </span>
                              <span>
                                <strong>
                                  {uiNode(
                                    task.title_zh || task.title_orig ? (
                                      <span data-no-localize>
                                        {task.title_zh || task.title_orig}
                                      </span>
                                    ) : (
                                      "等待读取视频信息"
                                    ),
                                  )}
                                </strong>
                                <small>
                                  {task.video_id} ·{" "}
                                  {task.account_name_snapshot ||
                                    uiText("历史账号待确认")}
                                  {uiNode(
                                    task.account_uid_snapshot
                                      ? ` (UID ${task.account_uid_snapshot})`
                                      : "",
                                  )}
                                  {uiNode(
                                    task.imported_history && " · 导入记录",
                                  )}
                                  {task.uploader && ` · ${task.uploader}`}
                                </small>
                              </span>
                            </span>
                            <span>
                              <Status status={task.status} />
                              {active(task) && progress[task.task_id] && (
                                <>
                                  <span className="mini-progress">
                                    <i
                                      style={{
                                        width: `${progress[task.task_id].percent ?? 25}%`,
                                      }}
                                    />
                                  </span>
                                  {progressSpeed(progress[task.task_id]) && (
                                    <small className="progress-speed">
                                      {uiNode(
                                        progressSpeed(progress[task.task_id]),
                                      )}
                                    </small>
                                  )}
                                </>
                              )}
                            </span>
                            <time>
                              {uiNode(
                                new Date(
                                  task.updated_at || task.created_at,
                                ).toLocaleString(getUiLanguage(), {
                                  month: "2-digit",
                                  day: "2-digit",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                }),
                              )}
                            </time>
                            <ChevronRight size={17} />
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-state">
                        <div className="empty-art">
                          <span />
                          <span />
                          <div>
                            <Link2 size={30} strokeWidth={1.4} />
                          </div>
                        </div>
                        <h2>
                          {uiNode(
                            query || filter
                              ? "没有找到匹配的任务"
                              : page === "history"
                                ? "还没有投稿记录"
                                : "你的下一条视频，从这里开始",
                          )}
                        </h2>
                        <p>
                          {uiNode(
                            query || filter
                              ? "试试其他关键词，或查看全部任务。"
                              : page === "history"
                                ? "完成投稿后，BV 号和提交记录会保留在这里。"
                                : "粘贴 YouTube 链接，我们会为你下载、校验并翻译素材。",
                          )}
                        </p>
                        {!query && !filter && page === "tasks" && (
                          <button
                            className="secondary"
                            onClick={() => setNewTask("publish")}
                            disabled={!connected}
                          >
                            <Plus size={16} />
                            {uiText("添加第一个任务")}
                          </button>
                        )}
                        <div className="workflow-hint">
                          <span>{uiText("01 下载")}</span>
                          <ArrowRight size={12} />
                          <span>{uiText("02 校验")}</span>
                          <ArrowRight size={12} />
                          <span>{uiText("03 预览与投稿")}</span>
                        </div>
                      </div>
                    )}
                  </ResizingList>
                  <div className="panel-footer">
                    <span>
                      {uiNode(
                        page === "tasks"
                          ? "默认先准备素材，由你确认后投稿"
                          : "已提交不代表通过平台审核",
                      )}
                    </span>
                    <div>
                      <span>
                        {uiNode(
                          listLoading
                            ? "…"
                            : uiText("{p0} 条记录", { p0: total }),
                        )}
                      </span>
                      <button
                        aria-label={uiText("上一页")}
                        className="icon-button"
                        disabled={listLoading || !offset}
                        onClick={() =>
                          setOffset((v) => Math.max(0, v - PAGE_SIZE))
                        }
                      >
                        <ChevronLeft size={15} />
                      </button>
                      <button
                        aria-label={uiText("下一页")}
                        className="icon-button"
                        disabled={listLoading || offset + PAGE_SIZE >= total}
                        onClick={() => setOffset((v) => v + PAGE_SIZE)}
                      >
                        <ChevronRight size={15} />
                      </button>
                    </div>
                  </div>
                </div>
                <div className="page-footnote">
                  <ShieldCheck size={14} />
                  {uiText(
                    "仅处理你拥有版权或已获授权的视频。下载、校验、上传各自排队，互不阻塞。",
                  )}
                </div>
              </>
            )}
            {page === "account" && config && (
              <Account
                config={config}
                auth={auth}
                busy={busy}
                action={action}
                refresh={async () => {
                  await loadConfig();
                  await refreshAccount();
                }}
                setAuth={setAuth}
              />
            )}
            {page === "settings" && config && (
              <Settings
                paused={shutdownStarted}
                config={config}
                busy={busy}
                action={action}
                refresh={loadConfig}
                diagnostics={diagnostics}
              />
            )}
          </Suspense>
        </main>
        <footer className="bottom-bar">
          <span>
            <Activity size={12} />
            {uiNode(
              queueActive.length
                ? uiText("{p0} 个任务正在队列中", { p0: queueActive.length })
                : "所有队列空闲",
            )}
          </span>
          <span>{uiText("本地处理 · 由你掌控")}</span>
        </footer>
      </div>
      {setup && config && (
        <Modal
          title={uiText("欢迎使用 screator")}
          close={() => {
            if (!busy)
              action(async () => {
                await request("auth.login.cancel");
                setSetup(false);
              });
          }}
        >
          <Suspense
            fallback={
              <div className="help" role="status">
                {uiText("加载中…")}
              </div>
            }
          >
            <SetupWizard
              config={config}
              auth={auth}
              diagnostics={diagnostics}
              busy={busy}
              action={action}
              refresh={async () => {
                await loadConfig();
                await refreshAccount();
              }}
              setDiagnostics={setDiagnostics}
              done={() => setSetup(false)}
            />
          </Suspense>
        </Modal>
      )}
      {confirmDeleteAllHistory && (
        <Modal
          title={uiText("删除所有投稿记录")}
          close={() => {
            if (!busy) setConfirmDeleteAllHistory(false);
          }}
        >
          <p className="modal-copy">
            {uiText(
              "确定删除本机全部投稿记录？包括已提交、待核对和导入的记录。平台上的稿件、本机素材及未完成的任务不会删除；记录删除后只能从备份或导出的文件恢复。",
            )}
          </p>
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirmDeleteAllHistory(false)}
            >
              {uiText("取消")}
            </button>
            <button
              className="secondary danger"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await request("history.delete_all", { confirmed: true });
                  setConfirmDeleteAllHistory(false);
                  latestTasks.current = {};
                  setOffset(0);
                  await loadTasks();
                  showSuccess("投稿记录已全部删除。");
                })
              }
            >
              {uiText("确认删除所有投稿记录")}
            </button>
          </div>
        </Modal>
      )}
      {newTask && (
        <NewTask
          kind={newTask}
          accounts={auth.accounts || []}
          config={config}
          busy={busy}
          action={action}
          close={() => {
            if (!busy) setNewTask(null);
          }}
          done={async () => {
            setNewTask(null);
            await loadTasks();
          }}
        />
      )}
      {selected &&
        (selected.imported_history ? (
          <ImportedHistoryDetail
            task={selected}
            busy={busy}
            onDelete={deleteHistory}
            close={() => setSelected(null)}
          />
        ) : selected.source_kind === "material" ? (
          <Modal title={uiText("素材下载记录")} close={() => setSelected(null)}>
            <Status status={selected.status} />
            <h3 data-no-localize>{selected.title_orig}</h3>
            <p className="material-path" data-no-localize>
              {selected.url}
            </p>
            <p>
              {uiText(
                "素材文件在素材管理中预览、重命名或删除。重复下载不会查重。",
              )}
            </p>
            <ReportError message={selected.error} />
            <div className="modal-actions">
              <button
                className="secondary"
                onClick={() => {
                  setSelected(null);
                  navigate("materials");
                }}
              >
                {uiText("查看素材库")}
              </button>
              {retryable(selected) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await request("tasks.retry", {
                        task_id: selected.task_id,
                        operation_id: operationId(),
                      });
                      setSelected(null);
                      await loadTasks();
                    })
                  }
                >
                  {uiText("继续任务")}
                </button>
              )}
              {active(selected) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await request("tasks.cancel", {
                        task_id: selected.task_id,
                      });
                      setSelected(null);
                      await loadTasks();
                    })
                  }
                >
                  {uiText("取消任务")}
                </button>
              )}
            </div>
          </Modal>
        ) : (
          <TaskDetail
            task={selected}
            onDelete={deleteHistory}
            accounts={accountOptions}
            progress={progress[selected.task_id]}
            progressMap={progress}
            busy={busy}
            action={action}
            close={() => setSelected(null)}
            refresh={async () => {
              const taskId = selected.task_id;
              await loadTasks();
              const updated = await request<Task>("tasks.get", {
                task_id: taskId,
              });
              setSelected((current) =>
                current?.task_id === taskId ? updated : current,
              );
            }}
          />
        ))}
      {closing && (
        <Modal
          title={uiText("退出 screator")}
          close={() => {
            if (!shutdownStarted) setClosing(false);
          }}
        >
          <p className="modal-copy">
            {uiNode(
              shutdownStarted
                ? !shutdownStatus?.closing
                  ? "正在通知后台停止任务并保存状态…"
                  : shutdownStatus.ready
                    ? "正在释放后台资源并退出…"
                    : uiText(
                        "已停止未投稿任务并保留素材。仍有 {p0} 个在途投稿、{p1} 个任务待收尾；投稿结束后自动退出。",
                        {
                          p0: shutdownStatus.inflight_task_ids.length,
                          p1: shutdownStatus.pending_task_ids.length,
                        },
                      )
                : "退出会取消未投稿任务，并等待正在投稿的账号完成。",
            )}
          </p>
          {shutdownStarted && shutdownStatus && !shutdownStatus.ready && (
            <p className="help">
              {uiNode(
                shutdownStatus.inflight_task_ids.length > 0
                  ? "正在等待平台返回投稿结果。"
                  : shutdownStatus.translation_active
                    ? "正在停止翻译组件操作。"
                    : shutdownStatus.stages?.length
                      ? "正在停止下载、校验或素材准备。"
                      : "正在保存任务状态。",
              )}
            </p>
          )}
          {shutdownStarted && (
            <p className="help">
              {uiText(
                "如需立即退出，可停止在途投稿。素材会保留；投稿请求可能已到达平台，重启后请核对结果，再决定是否重试。",
              )}
            </p>
          )}
          <div className="modal-actions">
            {!shutdownStarted && (
              <button className="secondary" onClick={() => setClosing(false)}>
                {uiText("继续使用")}
              </button>
            )}
            {shutdownStarted && (
              <button
                className="secondary"
                disabled={forceClosing}
                onClick={() => {
                  setForceClosing(true);
                  void forceCloseApp().catch((error) => {
                    setForceClosing(false);
                    showError(String(error));
                  });
                }}
              >
                {uiText("停止投稿并退出")}
              </button>
            )}
            <button
              disabled={shutdownStarted || busy}
              className="primary"
              onClick={() =>
                action(async () => {
                  shutdownRef.current = true;
                  beginShutdown();
                  setShutdownStarted(true);
                  const state = (await request(
                    "system.prepare_shutdown",
                  )) as ShutdownStatus;
                  setShutdownStatus(state);
                })
              }
            >
              {uiNode(shutdownStarted ? "等待安全退出…" : "退出应用")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

type Action = (work: () => Promise<unknown>, success?: string) => Promise<void>;
type NewTaskProps = {
  accounts: BiliAccount[];
  config: Config | null;
  busy: boolean;
  action: Action;
  close: () => void;
  done: () => Promise<void>;
};
function NewTask(props: NewTaskProps & { kind: "download" | "publish" }) {
  const [publishType, setPublishType] = useState("video");
  return (
    <Modal
      title={uiText(props.kind === "download" ? "新建下载素材任务" : "新建投稿任务")}
      close={props.close}
    >
      {props.kind === "download" ? (
        <DownloadTask {...props} />
      ) : (
        <>
          <div
            className="tabs upload-type-tabs"
            role="tablist"
            aria-label={uiText("上传类型")}
          >
            <button
              role="tab"
              aria-selected={publishType === "video"}
              className={publishType === "video" ? "current" : ""}
              disabled={props.busy}
              onClick={() => setPublishType("video")}
            >
              <Video size={16} />
              {uiText("视频上传")}
            </button>
            <button
              role="tab"
              aria-selected={publishType === "image"}
              className={publishType === "image" ? "current" : ""}
              disabled={props.busy}
              onClick={() => setPublishType("image")}
            >
              <ImagePlus size={16} />
              {uiText("图文上传")}
            </button>
          </div>
          <div
            role="tabpanel"
            aria-label={uiText("视频上传")}
            hidden={publishType !== "video"}
          >
            <PublishTask {...props} />
          </div>
          <div
            role="tabpanel"
            aria-label={uiText("图文上传")}
            hidden={publishType !== "image"}
          >
            <ImagePostForm {...props} />
          </div>
        </>
      )}
    </Modal>
  );
}
function DownloadTask({ busy, action, close, done }: NewTaskProps) {
  const [url, setUrl] = useState("");
  const [library, setLibrary] = useState("");
  const [type, setType] = useState("video");
  const op = useRef(operationId());
  return (
    <>
      <p className="modal-copy">
        {uiText("下载到指定素材库，仅保留下载记录，支持重复下载。")}
      </p>
      <div className="task-form-fields">
        <label className="field">
          {uiText("素材链接")}
          <input
            autoFocus
            type="url"
            placeholder="https://…"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              op.current = operationId();
            }}
          />
        </label>
        <label className="field">
          {uiText("素材类型")}
          <StyledSelect
            label="素材类型"
            value={type}
            onChange={(value) => {
              setType(value);
              op.current = operationId();
            }}
            options={[
              { value: "video", label: "视频" },
              { value: "image", label: "图片" },
              { value: "audio", label: "音频" },
            ]}
          />
        </label>
        <LibraryPicker
          value={library}
          onChange={(value) => {
            setLibrary(value);
            op.current = operationId();
          }}
          action={action}
        />
      </div>
      <details className="download-platforms">
        <summary>{uiText("支持的平台与下载说明")}</summary>
        <p>
          {uiText(
            "视频 / 音频：YouTube、Bilibili、AcFun、抖音、TikTok、小红书、微博、X、Instagram、Facebook、Vimeo、Dailymotion、Twitch、SoundCloud、Bandcamp 等网站。",
          )}
        </p>
        <p>
          {uiText(
            "图片：Instagram、X、Pinterest、Flickr、Reddit、Tumblr、DeviantArt、Pixiv 等图片帖子。",
          )}
        </p>
        <p>
          {uiText(
            "也支持视频、图片和音频文件直链。音频模式可提取视频中的音轨。每次输入一个作品链接，图片帖子最多 100 张。",
          )}
        </p>
        <p>
          {uiText(
            "站点登录、地区限制或网站改版可能导致下载失败。请下载你拥有使用权的素材。",
          )}
        </p>
      </details>
      <div className="modal-actions">
        <button className="secondary" onClick={close} disabled={busy}>
          {uiText("取消")}
        </button>
        <button
          className="primary"
          disabled={busy || !url.trim() || !library}
          onClick={() =>
            action(async () => {
              await request("materials.download", {
                url,
                library_id: library,
                asset_type: type,
                operation_id: op.current,
              });
              await done();
            }, "素材下载已加入队列。")
          }
        >
          {uiText("加入下载队列")}
        </button>
      </div>
    </>
  );
}

function PublishTask({
  accounts,
  config,
  busy,
  action,
  close,
  done,
}: {
  accounts: BiliAccount[];
  config: Config | null;
  busy: boolean;
  action: Action;
  close: () => void;
  done: () => Promise<void>;
}) {
  const [source, setSource] = useState("youtube");
  const [localPath, setLocalPath] = useState("");
  const [coverPath, setCoverPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [accountId, setAccountId] = useState("");
  const [text, setText] = useState("");
  const [mode, setMode] = useState("preview");
  const [authorized, setAuthorized] = useState(false);
  const [douyin, setDouyin] = useState<any>(null);
  const [syncDouyin, setSyncDouyin] = useState(false);
  const [acfun, setAcfun] = useState<any>(null);
  const [syncAcfun, setSyncAcfun] = useState(false);
  const [youtubeUpload, setYoutubeUpload] = useState<any>(null);
  const [syncYoutube, setSyncYoutube] = useState(false);
  const [syncSocial, setSyncSocial] = useState<SocialTarget[]>([]);
  useEffect(() => {
    let alive = true;
    request("douyin.auth.status")
      .then((status) =>
        status.account
          ? request("douyin.auth.status", { verify: true })
          : status,
      )
      .then((s) => {
        if (alive) setDouyin(s);
      })
      .catch(() => {});
    request("youtube.auth.status", { verify: true })
      .then((s) => {
        if (alive) setYoutubeUpload(s);
      })
      .catch(() => {});
    request("acfun.auth.status", { verify: true })
      .then((s) => {
        if (alive) setAcfun(s);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const op = useRef(operationId());
  return (
    <>
      <p className="modal-copy">
        {uiText("每次添加一个视频，并选择本次投稿的账号。")}
      </p>
      <div
        className="task-source-tabs"
        role="group"
        aria-label={uiText("投稿来源")}
      >
        <button
          className={source === "youtube" ? "primary" : "secondary"}
          onClick={() => {
            setSource("youtube");
            op.current = operationId();
          }}
        >
          {uiText("YouTube 链接")}
        </button>
        <button
          className={source === "local" ? "primary" : "secondary"}
          onClick={() => {
            setSource("local");
            op.current = operationId();
          }}
        >
          {uiText("本地视频")}
        </button>
      </div>
      <div className="task-form-fields">
        {source === "local" ? (
          <>
            <div className="file-field">
              <label className="field">
                {uiText("视频文件")}
                <input
                  aria-label={uiText("视频文件路径")}
                  placeholder={uiText("选择或输入本地视频绝对路径")}
                  value={localPath}
                  onChange={(e) => {
                    setLocalPath(e.target.value);
                    op.current = operationId();
                  }}
                />
              </label>
              <div className="button-row">
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      const path = await chooseFile([
                        "mp4",
                        "mkv",
                        "webm",
                        "mov",
                        "avi",
                        "m4v",
                        "flv",
                      ]);
                      if (path) {
                        setLocalPath(path);
                        op.current = operationId();
                      }
                    })
                  }
                >
                  <FolderOpen size={15} />
                  {uiText("选择视频文件")}
                </button>
              </div>
            </div>
            <div className="file-field">
              <label className="field">
                {uiText("封面图片（可选）")}
                <input
                  aria-label={uiText("封面文件路径")}
                  placeholder={uiText("选择或输入封面图片绝对路径")}
                  value={coverPath}
                  onChange={(e) => {
                    setCoverPath(e.target.value);
                    op.current = operationId();
                  }}
                />
              </label>
              <div className="button-row">
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      const path = await chooseFile([
                        "jpg",
                        "jpeg",
                        "png",
                        "webp",
                        "bmp",
                      ]);
                      if (path) {
                        setCoverPath(path);
                        op.current = operationId();
                      }
                    })
                  }
                >
                  <FolderOpen size={15} />
                  {uiText("选择封面图片")}
                </button>
                {coverPath && (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() => {
                      setCoverPath("");
                      op.current = operationId();
                    }}
                  >
                    {uiText("清除封面")}
                  </button>
                )}
              </div>
              <p className="help">
                {uiText(
                  "支持 JPG、PNG、WebP、BMP；图片会裁剪为 16:9。未选择时自动从视频抽帧。",
                )}
              </p>
            </div>
            <label className="field">
              {uiText("标题")}
              <input
                maxLength={80}
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  op.current = operationId();
                }}
              />
            </label>
            <label className="field">
              {uiText("简介")}
              <textarea
                rows={3}
                maxLength={syncAcfun ? 1000 : 2000}
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  op.current = operationId();
                }}
              />
            </label>
            <p className="help">
              {uiText(
                "跳过下载与翻译，校验后投稿。投稿成功后保留源视频和任务工作目录。",
              )}
            </p>
          </>
        ) : (
          <label className="field">
            {uiText("YouTube 视频链接")}
            <input
              autoFocus
              type="url"
              placeholder="https://www.youtube.com/watch?v=…"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                op.current = operationId();
              }}
            />
          </label>
        )}
        <AccountSelector
          accounts={accounts}
          value={accountId}
          onChange={(id) => {
            setAccountId(id);
            op.current = operationId();
          }}
        />
        {!accounts.length && (
          <p className="help">
            {uiText("请先到账号与连接添加 Bilibili 账号。")}
          </p>
        )}
      </div>
      <div className="mode-options">
        <label className={mode === "preview" ? "chosen" : ""}>
          <input
            type="radio"
            name="mode"
            checked={mode === "preview"}
            onChange={() => {
              setMode("preview");
              op.current = operationId();
            }}
          />
          <span>
            <strong>
              {uiText("准备素材并预览 ")}
              <em>{uiText("推荐")}</em>
            </strong>
            <small>
              {uiNode(
                source === "local"
                  ? "校验完成后，由你确认投稿。"
                  : "下载、校验和翻译完成后，由你确认投稿。",
              )}
            </small>
          </span>
        </label>
        <label className={mode === "auto" ? "chosen" : ""}>
          <input
            type="radio"
            name="mode"
            checked={mode === "auto"}
            onChange={() => {
              setMode("auto");
              op.current = operationId();
            }}
          />
          <span>
            <strong>{uiText("自动投稿")}</strong>
            <small>{uiText("素材准备好后，自动提交到本次选择的平台。")}</small>
          </span>
        </label>
      </div>
      <div className="new-task-choices">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={syncDouyin}
            disabled={!douyin?.can_sync}
            onChange={(e) => {
              setSyncDouyin(e.target.checked);
              op.current = operationId();
            }}
          />
          {uiText("同步上传抖音")}
          {douyin?.account ? ` · ${douyin.account.nickname}` : ""}
        </label>
        {!douyin?.can_sync && (
          <p className="help">
            {uiText("请先在“账号与连接”登录抖音，登录后才能勾选。")}
          </p>
        )}
        {syncDouyin && (
          <p className="help">
            {uiText(
              "共用素材，两平台独立排队。预览模式一起确认；自动模式分别自动投稿。",
            )}
          </p>
        )}
        <ReportError
          message={
            syncDouyin && mode === "auto" && !douyin?.capabilities?.auto_publish
              ? "当前抖音服务未启用官方批准的自动发布能力，请选择预览模式。"
              : null
          }
        />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={syncAcfun}
            disabled={!acfun?.can_sync}
            onChange={(e) => {
              setSyncAcfun(e.target.checked);
              op.current = operationId();
            }}
          />
          {uiText("同步上传 AcFun")}
          {acfun?.account ? ` · ${acfun.account.nickname}` : ""}
        </label>
        {!acfun?.can_sync && (
          <p className="help">
            {uiNode(
              acfun?.account?.auth_state === "valid"
                ? "AcFun 账号已登录，但后台仍使用旧版配置。请使用最新构建，完整退出并重新打开应用后重试。"
                : acfun?.account
                  ? "AcFun 登录已失效，请在“账号与连接”重新扫码。"
                  : "请先在“账号与连接”扫码登录 AcFun。",
            )}
          </p>
        )}
        {acfun?.account?.auth_state === "valid" && (
          <p className="help">
            {uiText(
              "AcFun 使用独立队列，并共用本任务的标签与简介；标题最多 50 字。",
            )}
            {uiNode(
              mode === "auto" ? "素材准备好后自动投稿。" : "预览后确认投稿。",
            )}
          </p>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={syncYoutube}
            disabled={!youtubeUpload?.can_sync}
            onChange={(e) => {
              setSyncYoutube(e.target.checked);
              op.current = operationId();
            }}
          />
          {uiText("同步上传 YouTube")}
          {youtubeUpload?.account ? ` · ${youtubeUpload.account.nickname}` : ""}
        </label>
        <p className="help">
          {uiNode(
            youtubeUpload?.can_sync
              ? "YouTube 使用独立队列，共用视频、封面、简介和标签。预览时可单独编辑标题、分类与可见性。"
              : "请先在“账号与连接”配置并授权 YouTube 频道。",
          )}
        </p>
        <ReportError
          message={
            syncAcfun && mode === "auto" && !config?.acfun_channel_id
              ? "请先在设置中填写 AcFun 默认分区 ID，再创建自动投稿任务。"
              : null
          }
        />
        <SocialTargets
          mode={mode}
          value={syncSocial}
          onChange={(value) => {
            setSyncSocial(value);
            op.current = operationId();
          }}
        />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={authorized}
            onChange={(e) => setAuthorized(e.target.checked)}
          />
          {uiText("我拥有该视频的版权或已获得转载授权。")}
        </label>
      </div>
      <div className="modal-actions">
        <button className="secondary" onClick={close} disabled={busy}>
          {uiText("取消")}
        </button>
        <button
          className="primary"
          disabled={
            busy ||
            (source === "local"
              ? !localPath.trim() || !title.trim()
              : !text.trim()) ||
            !authorized ||
            !accountId ||
            (mode === "auto" &&
              syncSocial.some((target) => target.platform === "tiktok")) ||
            (syncDouyin &&
              mode === "auto" &&
              !douyin?.capabilities?.auto_publish) ||
            (syncAcfun &&
              mode === "auto" &&
              (!acfun?.capabilities?.auto_publish || !config?.acfun_channel_id))
          }
          onClick={() =>
            action(async () => {
              const result = await request("tasks.create", {
                url: source === "local" ? "" : text,
                ...(source === "local"
                  ? {
                      local_path: localPath,
                      cover_path: coverPath.trim(),
                      title,
                      description,
                    }
                  : {}),
                account_id: accountId,
                mode,
                ...(syncDouyin
                  ? {
                      sync_douyin: true,
                      douyin_account_id: douyin.account.account_id,
                      douyin_binding_revision: douyin.account.binding_revision,
                    }
                  : {}),
                ...(syncAcfun
                  ? {
                      sync_acfun: true,
                      acfun_account_id: acfun.account.account_id,
                      acfun_binding_revision: acfun.account.binding_revision,
                    }
                  : {}),
                sync_social: syncSocial,
                ...(syncYoutube
                  ? {
                      sync_youtube: true,
                      youtube_account_id: youtubeUpload.account.account_id,
                      youtube_binding_revision:
                        youtubeUpload.account.binding_revision,
                    }
                  : {}),
                operation_id: op.current,
              });
              if (!result.created)
                throw new Error("该账号已有此视频任务，请在列表中查看或继续。");
              await done();
            }, "任务已加入队列。")
          }
        >
          {busy ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
          {uiText("加入队列")}
        </button>
      </div>
    </>
  );
}

function ImportedHistoryDetail({
  task,
  busy,
  onDelete,
  close,
}: {
  task: Task;
  busy: boolean;
  onDelete: (task: Task) => Promise<void>;
  close: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const platforms = {
    bilibili: "Bilibili",
    douyin: "抖音",
    acfun: "AcFun",
    youtube: "YouTube",
    twitter: "X（Twitter）",
    facebook: "Facebook Page",
    tiktok: "TikTok",
    xiaohongshu: "小红书",
  };
  return (
    <Modal title={uiText("导入的投稿记录")} close={close} wide>
      <div className="section-body history-detail">
        <p className="help">
          {uiText(
            "这是一条只读历史记录，不包含登录凭据或视频素材，也不会加入投稿队列。",
          )}
        </p>
        <h3 data-no-localize>
          {task.title_zh || task.title_orig || task.video_id}
        </h3>
        <p>
          <Status status={task.status} /> · {task.video_id}
        </p>
        <p>
          {uiText("账号：")}
          {task.account_name_snapshot || uiText("原账号")}
          {uiNode(
            task.account_uid_snapshot
              ? ` · UID ${task.account_uid_snapshot}`
              : "",
          )}
        </p>
        <p>
          {uiText("创建：")}
          {uiNode(task.created_at || "未知")}
          {uiText(" · 更新：")}
          {uiNode(task.updated_at || "未知")}
        </p>
        {task.url && (
          <p>
            {uiText("来源：")}
            {task.url}
          </p>
        )}
        {task.publications?.map((pub) => (
          <div className="queue-card" key={pub.platform}>
            <strong>
              {uiNode(platforms[pub.platform])} ·{" "}
              {uiNode(labels[pub.status] || pub.status)}
            </strong>
            {pub.account_label && (
              <p>
                {uiText("投稿账号：")}
                {pub.account_label}
              </p>
            )}
            {pub.remote_id && (
              <p>
                {uiText("稿件号：")}
                {pub.remote_id}
              </p>
            )}
            {pub.text && (
              <p>
                {uiText("投稿标题：")}
                <span data-no-localize>{pub.text}</span>
              </p>
            )}
            {pub.error && (
              <p>
                {uiText("备注：")}
                <span>{uiText(pub.error)}</span>
              </p>
            )}
          </div>
        ))}
        {task.desc_zh && (
          <div>
            <strong>{uiText("中文简介")}</strong>
            <pre data-no-localize>{task.desc_zh}</pre>
          </div>
        )}
        {task.desc_orig && (
          <div>
            <strong>{uiText("原始简介")}</strong>
            <pre data-no-localize>{task.desc_orig}</pre>
          </div>
        )}
        {confirmDelete && (
          <div className="confirm-box">
            <strong>
              {uiText(
                "确定从本机删除这条导入记录？不会删除平台上的稿件；重新导入原文件可以恢复。",
              )}
            </strong>
            <div>
              <button
                className="secondary"
                onClick={() => setConfirmDelete(false)}
              >
                {uiText("返回")}
              </button>
              <button
                className="secondary danger"
                disabled={busy}
                onClick={() => void onDelete(task)}
              >
                {uiText("确认删除记录")}
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="modal-actions">
        <button
          className="secondary danger"
          disabled={busy}
          onClick={() => setConfirmDelete(true)}
        >
          {uiText("删除投稿记录")}
        </button>
        <button className="secondary" onClick={close}>
          {uiText("关闭")}
        </button>
      </div>
    </Modal>
  );
}

function TaskDetail({
  accounts,
  task,
  onDelete,
  progress,
  progressMap,
  busy,
  action,
  close,
  refresh,
}: {
  accounts: BiliAccount[];
  task: Task;
  onDelete: (task: Task) => Promise<void>;
  progress?: Progress;
  progressMap: Record<string, Progress>;
  busy: boolean;
  action: Action;
  close: () => void;
  refresh: () => Promise<void>;
}) {
  const { showSuccess } = useToast();
  const [legacyAccount, setLegacyAccount] = useState("");
  const [tab, setTab] = useState("metadata");
  const [title, setTitle] = useState(task.title_zh);
  const [description, setDescription] = useState(task.desc_zh);
  const [cover, setCover] = useState<string | null>(null);
  const [postImages, setPostImages] = useState<PostImage[]>([]);
  const [postImagesError, setPostImagesError] = useState("");
  const isImagePost = task.source_kind === "image_post";
  const [logs, setLogs] = useState<any[]>([]);
  const [confirm, setConfirm] = useState("");
  const [retryOptions, setRetryOptions] = useState<{
    useCurrentTranslationSettings: boolean;
  } | null>(null);
  const [retryMode, setRetryMode] = useState<"preview" | "auto">("preview");
  const [bv, setBv] = useState("");
  const [douyinDirty, setDouyinDirty] = useState(false);
  const descriptionLimit = task.publications?.some(
    (p) =>
      (p.platform === "acfun" || p.platform === "xiaohongshu") &&
      p.status === "ready",
  )
    ? 1000
    : 2000;
  const op = useRef(operationId());
  const chooseRetryMode = (useCurrentTranslationSettings = false) => {
    setRetryMode(task.snapshot?.mode === "auto" ? "auto" : "preview");
    setRetryOptions({ useCurrentTranslationSettings });
    op.current = operationId();
  };
  useEffect(() => {
    let alive = true;
    setPostImages([]);
    setPostImagesError("");
    if (isImagePost)
      request<{ items: PostImage[] }>("image_posts.images", {
        task_id: task.task_id,
      })
        .then((value) => {
          if (alive) setPostImages(value.items);
        })
        .catch((error) => {
          if (alive) setPostImagesError(String(error));
        });
    return () => {
      alive = false;
    };
  }, [isImagePost, task.task_id, task.image_paths]);
  useEffect(() => {
    setTitle(task.title_zh);
    setDescription(task.desc_zh);
  }, [task.task_id, task.title_zh, task.desc_zh]);
  useEffect(() => {
    let alive = true;
    request("tasks.cover", { task_id: task.task_id })
      .then((value) => {
        if (alive) setCover(value.image);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [task.task_id, task.cover_path]);
  useEffect(() => {
    if (tab !== "logs") return;
    const load = () =>
      request("logs.tail", { task_id: task.task_id })
        .then((value) => setLogs(value.items))
        .catch(() => {});
    void load();
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [task.task_id, tab]);
  const command = (method: string, success: string) =>
    action(async () => {
      await request(method, {
        task_id: task.task_id,
        operation_id: op.current,
        ...(method === "tasks.submit" &&
        task.publications?.some((p) => p.platform !== "bilibili")
          ? {
              targets: task.publications
                .filter((p) => p.status === "ready")
                .map((p) => p.publication_id),
              revisions: Object.fromEntries(
                task.publications.map((p) => [p.publication_id, p.revision]),
              ),
            }
          : {}),
      });
      op.current = operationId();
      setConfirm("");
      await refresh();
    }, success);
  return (
    <Modal title={uiText("任务详情")} close={close} wide>
      <div className="detail-summary">
        {cover ? (
          <img
            className="cover"
            src={cover}
            alt={uiText(isImagePost ? "图文首图" : "视频封面")}
          />
        ) : (
          <div className="cover placeholder">
            {isImagePost ? <ImagePlus size={35} /> : <Video size={35} />}
          </div>
        )}
        <div>
          <Status status={task.status} />
          <h3 data-no-localize>
            {task.title_zh || task.title_orig || task.video_id}
          </h3>
          <p>
            {isImagePost
              ? uiText("Bilibili 图文动态")
              : task.uploader || uiText("等待读取作者")}{" "}
            · {task.video_id}
            <br />
            {task.account_name_snapshot || uiText("历史账号待确认")}
            {uiNode(
              task.account_uid_snapshot
                ? ` · UID ${task.account_uid_snapshot}`
                : "",
            )}
          </p>
          <button
            className="text-button"
            onClick={() =>
              action(async () => {
                await request("tasks.open_folder", { task_id: task.task_id });
              })
            }
            disabled={!task.work_dir}
          >
            <FolderOpen size={14} />
            {uiText("打开素材目录")}
          </button>
          {task.bv_id && (
            <button
              className="text-button"
              onClick={() =>
                external(
                  isImagePost
                    ? `https://t.bilibili.com/${task.bv_id}`
                    : `https://www.bilibili.com/video/${task.bv_id}`,
                )
              }
            >
              <ExternalLink size={14} />
              {task.bv_id}
            </button>
          )}
        </div>
      </div>
      <ReportError message={task.error} />
      {task.publications?.some((p) => p.platform !== "bilibili") && (
        <PublicationDetails
          task={task}
          busy={busy}
          action={action}
          refresh={refresh}
          dirty={douyinDirty}
          onDirtyChange={setDouyinDirty}
          progressMap={progressMap}
        />
      )}
      {active(task) && (
        <div className="detail-progress">
          <span>
            {uiNode(labels[progress?.stage || task.status])}
            {uiNode(
              progress?.provider
                ? ` · ${progress.provider === "local_llm" ? "本地大模型" : "DeepL"}`
                : "",
            )}
            {uiNode(
              progress?.track && ` · ${progress.track} · ${progress.backend}`,
            )}{" "}
            {uiNode(progressSpeed(progress) && `· ${progressSpeed(progress)} `)}
            {uiNode(
              progress?.remaining
                ? uiText("· 剩余 {p0} 秒", {
                    p0: Math.ceil(progress.remaining),
                  })
                : "",
            )}
          </span>
          <strong>
            {uiNode(
              progress?.percent != null
                ? `${progress.percent.toFixed(1)}%`
                : "进行中",
            )}
          </strong>
          <div>
            <i
              className={progress?.percent == null ? "indeterminate" : ""}
              style={{ width: `${progress?.percent ?? 30}%` }}
            />
          </div>
        </div>
      )}
      {!task.account_id && (
        <div className="section-body">
          <p>{uiText("历史账号待确认：绑定后不可改投，请核对原投稿归属。")}</p>
          <AccountSelector
            accounts={accounts}
            archived={task.status === "submitted"}
            value={legacyAccount}
            onChange={setLegacyAccount}
          />
          <button
            disabled={busy || !legacyAccount}
            onClick={() =>
              action(async () => {
                await request("tasks.bind_legacy_account", {
                  task_id: task.task_id,
                  account_id: legacyAccount,
                });
                await refresh();
              })
            }
          >
            {uiText("确认历史账号")}
          </button>
        </div>
      )}
      {task.translation?.state === "queued" && (
        <p className="help">
          {uiText("旧译文已清空，")}
          {uiNode(
            task.status === "translating" ? "正在重新翻译。" : "等待重新翻译。",
          )}
        </p>
      )}
      {task.source_kind !== "local" &&
        !isImagePost &&
        task.translation &&
        !["queued", "failed", "cancelled"].includes(
          task.translation.state || "",
        ) && (
          <p className="help">
            {uiText("翻译来源：")}
            {uiNode(
              (
                {
                  local_llm: "本地大模型",
                  deepl: "DeepL",
                  none: "无需翻译",
                  unknown: "历史译文",
                } as Record<string, string>
              )[task.translation.provider || "unknown"],
            )}
            {uiNode(
              task.translation.fallback_used &&
                uiText(" · 已切换服务（{p0}）", {
                  p0: uiText(task.translation.fallback_reason || ""),
                }),
            )}
            {uiNode(
              task.translation.elapsed_ms != null &&
                uiText(" · {p0} 秒", {
                  p0: (task.translation.elapsed_ms / 1000).toFixed(1),
                }),
            )}
            {uiNode(task.translation.user_edited && " · 已人工修改")}
            {uiNode(task.translation.input_truncated && " · 原文片段已截短")}
          </p>
        )}
      <div className="tabs detail-tabs">
        <button
          className={tab === "metadata" ? "current" : ""}
          onClick={() => setTab("metadata")}
        >
          {uiText("投稿素材")}
        </button>
        <button
          className={tab === "original" ? "current" : ""}
          onClick={() => setTab("original")}
        >
          {uiText("原始信息")}
        </button>
        <button
          className={tab === "logs" ? "current" : ""}
          onClick={() => setTab("logs")}
        >
          {uiText("运行日志")}
        </button>
      </div>
      {tab === "metadata" && (
        <div className="detail-form">
          {isImagePost && (
            <>
              <ReportError message={postImagesError} />
              <PostImages images={postImages} />
            </>
          )}
          {!isImagePost && (
            <label className="field">
              {uiText("中文标题 ")}
              <span>{title.length} / 80</span>
              <input
                value={title}
                maxLength={80}
                readOnly={!editable(task)}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
          )}
          <label className="field">
            {uiNode(isImagePost ? "图文正文" : "简介")}{" "}
            <span>
              {description.length} / {descriptionLimit}
            </span>
            <textarea
              rows={7}
              value={description}
              maxLength={descriptionLimit}
              readOnly={!editable(task)}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <p className="help">
            {uiNode(
              isImagePost
                ? "动态发布后保留源图片和任务工作目录。"
                : task.source_kind === "local"
                  ? "投稿成功后保留源视频和任务工作目录。"
                  : "简介中会保留原作者。获得 BV 号后，任务素材将自动清理。",
            )}
          </p>
          {editable(task) && (
            <div className="inline-controls">
              <button
                className="secondary"
                disabled={busy || description.length > descriptionLimit}
                onClick={() =>
                  action(async () => {
                    await request("tasks.update_metadata", {
                      task_id: task.task_id,
                      title: isImagePost
                        ? description.trim().split("\n")[0].slice(0, 80)
                        : title,
                      description,
                    });
                    await refresh();
                  }, "投稿信息已保存。")
                }
              >
                <Check size={15} />
                {uiText("保存修改")}
              </button>
              {task.source_kind !== "local" && !isImagePost && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => setConfirm("retranslate")}
                >
                  {uiText("重新翻译")}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {tab === "original" && (
        <div className="original-info">
          <h3>
            {uiNode(
              task.title_orig ? (
                <span data-no-localize>{task.title_orig}</span>
              ) : (
                "尚未读取"
              ),
            )}
          </h3>
          <p>{task.url}</p>
          <pre>
            {uiNode(
              task.desc_orig ? (
                <span data-no-localize>{task.desc_orig}</span>
              ) : (
                "暂无原始简介"
              ),
            )}
          </pre>
        </div>
      )}
      {tab === "logs" && (
        <div className="log-view">
          {logs.length ? (
            logs.map((line, i) => (
              <div key={i}>
                <time>{line.time}</time>
                <span className={line.level === "ERROR" ? "error-text" : ""}>
                  {line.message}
                </span>
              </div>
            ))
          ) : (
            <p>{uiText("本次会话暂无该任务日志。")}</p>
          )}
        </div>
      )}
      {task.status === "submission_unknown" &&
        !task.publications?.some((p) => p.platform !== "bilibili") && (
          <div className="resolve-box">
            <strong>
              {uiNode(
                isImagePost
                  ? "先核对账号动态，再继续处理"
                  : "先核对创作中心，再继续处理",
              )}
            </strong>
            <p>
              {uiNode(
                isImagePost
                  ? "网络中断不一定代表发布失败。登记动态 ID 会保留本地图片。"
                  : "网络中断不一定代表投稿失败。登记 BV 号会保留本地素材。",
              )}
            </p>
            <button
              className="text-button"
              onClick={() =>
                external(
                  isImagePost
                    ? `https://space.bilibili.com/${task.account_uid_snapshot}/dynamic`
                    : "https://member.bilibili.com/platform/upload-manager/article",
                )
              }
            >
              {uiNode(isImagePost ? "打开账号动态" : "打开创作中心")}{" "}
              <ExternalLink size={14} />
            </button>
            <div className="inline-controls">
              <input
                aria-label={uiText(isImagePost ? "登记动态 ID" : "登记 BV 号")}
                value={bv}
                onChange={(e) => setBv(e.target.value)}
                placeholder={uiText(isImagePost ? "数字动态 ID" : "BV…")}
              />
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(
                    async () => {
                      await request("tasks.resolve", {
                        task_id: task.task_id,
                        bv_id: bv,
                      });
                      await refresh();
                    },
                    isImagePost ? "动态 ID 已登记。" : "BV 号已登记。",
                  )
                }
              >
                {uiText("登记已提交稿件")}
              </button>
            </div>
            <button
              className="text-button"
              onClick={() => setConfirm("resolve")}
            >
              {uiText("我已核对，确实没有提交")}
            </button>
          </div>
        )}
      {retryable(task) && task.source_kind !== "local" && !isImagePost && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => chooseRetryMode(true)}
        >
          {uiText("使用当前翻译设置重试")}
        </button>
      )}
      {retryOptions && (
        <Modal
          title={uiText("选择继续任务的投稿模式")}
          close={() => {
            if (!busy) setRetryOptions(null);
          }}
        >
          <p className="modal-copy">
            {uiText("请重新选择本次任务的投稿方式。")}
          </p>
          <div className="mode-options">
            <label className={retryMode === "preview" ? "chosen" : ""}>
              <input
                type="radio"
                name="retry-mode"
                checked={retryMode === "preview"}
                disabled={busy}
                onChange={() => {
                  setRetryMode("preview");
                  op.current = operationId();
                }}
              />
              <span>
                <strong>
                  {uiText("准备素材并预览 ")}
                  <em>{uiText("推荐")}</em>
                </strong>
                <small>{uiText("下载、校验和翻译完成后，由你确认投稿。")}</small>
              </span>
            </label>
            <label className={retryMode === "auto" ? "chosen" : ""}>
              <input
                type="radio"
                name="retry-mode"
                checked={retryMode === "auto"}
                disabled={busy}
                onChange={() => {
                  setRetryMode("auto");
                  op.current = operationId();
                }}
              />
              <span>
                <strong>{uiText("自动投稿")}</strong>
                <small>{uiText("素材准备好后，自动提交到本次选择的平台。")}</small>
              </span>
            </label>
          </div>
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setRetryOptions(null)}
            >
              {uiText("取消")}
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                void action(
                  async () => {
                    await request("tasks.retry", {
                      task_id: task.task_id,
                      operation_id: op.current,
                      mode: retryMode,
                      use_current_translation_settings:
                        retryOptions.useCurrentTranslationSettings,
                    });
                    op.current = operationId();
                    setRetryOptions(null);
                    await refresh();
                  },
                  retryMode === "auto"
                    ? "已继续准备素材，完成后自动投稿。"
                    : "已继续准备素材，完成后等待预览。",
                )
              }
            >
              {uiText("继续任务")}
            </button>
          </div>
        </Modal>
      )}
      {confirm && (
        <div className="confirm-box">
          <strong>
            {uiNode(
              confirm === "retranslate"
                ? "将立即清空当前中文标题和简介（含人工修改），按当前设置重新进入翻译队列。若翻译失败，旧译文不会恢复；完成后需重新预览。确定继续？"
                : confirm === "submit"
                  ? uiText("确认投稿到 {p0} · UID {p1}{p2}{p3}{p4}{p5}{p6}？", {
                      p0: task.account_name_snapshot,
                      p1: task.account_uid_snapshot,
                      p2: task.publications?.some(
                        (p) => p.platform === "douyin",
                      )
                        ? uiText("，并同步到抖音")
                        : "",
                      p3: task.publications?.some((p) => p.platform === "acfun")
                        ? uiText("，并同步到 AcFun")
                        : "",
                      p4: task.publications?.some(
                        (p) => p.platform === "youtube",
                      )
                        ? uiText("，并同步到 YouTube")
                        : "",
                      p5: task.publications?.some(
                        (p) => p.platform === "xiaohongshu",
                      )
                        ? uiText("，并同步到小红书")
                        : "",
                      p6: task.publications?.some((p) =>
                        ["twitter", "facebook", "tiktok"].includes(p.platform),
                      )
                        ? uiText("，并提交所选同步平台")
                        : "",
                    })
                  : confirm === "repair"
                    ? "准备原稿件的替换视频？此操作不会投稿。"
                    : confirm === "delete_history"
                      ? "确定从本机删除这条任务及其所有平台投稿记录？不会删除平台上的稿件。未投稿成功的任务会一并删除本机素材；已提交或待核对的任务会保留素材。删除后只能从备份恢复记录。"
                      : isImagePost
                        ? "确认账号动态中没有这条图文？"
                        : "确认创作中心没有这条稿件？",
            )}
          </strong>
          <div>
            <button className="secondary" onClick={() => setConfirm("")}>
              {uiText("返回")}
            </button>
            <button
              className={
                confirm === "delete_history" ? "secondary danger" : "primary"
              }
              disabled={busy}
              onClick={() =>
                confirm === "delete_history"
                  ? void onDelete(task)
                  : confirm === "retranslate"
                    ? action(async () => {
                        await request("tasks.retranslate", {
                          task_id: task.task_id,
                          operation_id: operationId(),
                          replace_edited: true,
                        });
                        setConfirm("");
                        await refresh();
                      }, "旧译文已清空，任务已进入翻译队列。")
                    : confirm === "submit"
                      ? command("tasks.submit", "已进入投稿队列。")
                      : confirm === "repair"
                        ? command("tasks.repair", "已开始准备替换素材。")
                        : action(async () => {
                            await request("tasks.resolve", {
                              task_id: task.task_id,
                              not_submitted: true,
                            });
                            setConfirm("");
                            await refresh();
                          }, "已标记为可继续处理。")
              }
            >
              {uiNode(confirm === "delete_history" ? "确认删除记录" : "确认")}
            </button>
          </div>
        </div>
      )}
      <div className="modal-actions">
        <span className="help">{uiText("已提交 ≠ 已过审")}</span>
        {!active(task) && (
          <button
            className="secondary danger"
            disabled={busy}
            onClick={() => setConfirm("delete_history")}
          >
            {uiText("删除投稿记录")}
          </button>
        )}
        <button className="secondary" onClick={close}>
          {uiText("关闭")}
        </button>
        {retryable(task) && task.account_id && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => chooseRetryMode()}
          >
            <RefreshCw size={15} />
            {uiText("继续任务")}
          </button>
        )}
        {editable(task) && (
          <button
            className="primary"
            disabled={
              busy ||
              !task.account_id ||
              title !== task.title_zh ||
              description !== task.desc_zh ||
              douyinDirty ||
              task.publications?.some(
                (pub) => pub.status === "ready" && !canConfirmPublication(pub),
              )
            }
            title={uiText("请先保存修改")}
            onClick={() => setConfirm("submit")}
          >
            <Send size={15} />
            {uiText("确认投稿")}
          </button>
        )}
        {task.status === "submitted" &&
          task.bv_id &&
          task.source_kind !== "local" &&
          !isImagePost && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm("repair")}
            >
              {uiText("准备修复素材")}
            </button>
          )}
        {active(task) && task.status !== "uploading" && (
          <button
            className="secondary danger"
            disabled={busy || task.status === "cancel_requested"}
            onClick={() =>
              action(async () => {
                const result = await request<{ status: string }>(
                  "tasks.cancel",
                  { task_id: task.task_id },
                );
                await refresh();
                showSuccess(
                  result.status === "cancelled"
                    ? "任务已取消，记录已保留。"
                    : "已请求取消，素材会保留。",
                );
              })
            }
          >
            {uiText("取消任务")}
          </button>
        )}
      </div>
    </Modal>
  );
}

function Account({
  config,
  auth,
  busy,
  action,
  refresh,
  setAuth,
}: {
  config: Config;
  auth: any;
  busy: boolean;
  action: Action;
  refresh: () => Promise<void>;
  setAuth: (v: any) => void;
}) {
  const { showSuccess } = useToast();
  const [showLogin, setShowLogin] = useState(false);
  const [browser, setBrowser] = useState("edge");
  const login = auth.login || {};
  const importCookie = (kind: string) =>
    action(async () => {
      const path = await chooseFile(kind === "bilibili" ? ["json"] : ["txt"]);
      if (path) {
        await request("auth.import", { path, kind });
        await refresh();
        showSuccess("Cookie 已导入本机。");
      }
    });
  const loginLabels: Record<string, string> = {
    loading: "正在获取二维码…",
    waiting: "请使用哔哩哔哩 App 扫码并确认",
    scanned: "已扫码，请在手机上确认",
    expired: "二维码已过期，请刷新",
    success: "登录成功",
    failed: "获取二维码失败",
    idle: "准备扫码登录",
  };
  return (
    <div className="settings-stack">
      <AccountsPanel auth={auth} refresh={refresh} />
      <DouyinAccountPanel />
      <AcfunAccountPanel />
      <XiaohongshuAccountPanel />
      <YoutubeAccountPanel />
      <SocialAccountPanels />
      <TranslationPanel
        config={config}
        busy={busy}
        action={action}
        refresh={refresh}
      />
      <section className="settings-card">
        <div className="section-title">
          <div className="service-icon">
            <Link2 size={23} />
          </div>
          <div>
            <h2>{uiText("YouTube 访问")}</h2>
            <p>{uiText("当源站要求登录验证时，提供你的浏览器 Cookie。")}</p>
          </div>
          <StatusBadge tone={config.youtube_cookies ? "success" : "error"}>
            {uiNode(config.youtube_cookies ? "已导入" : "未导入")}
          </StatusBadge>
        </div>
        <div className="section-body">
          <div className="button-row">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => importCookie("youtube")}
            >
              <Upload size={15} />
              {uiText("导入 Cookie 文件")}
            </button>
            <StyledSelect
              label="浏览器"
              value={browser}
              onChange={setBrowser}
              options={[
                { value: "edge", label: "Edge" },
                { value: "chrome", label: "Chrome" },
                { value: "firefox", label: "Firefox" },
              ]}
            />
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await request("auth.youtube_export", { browser });
                  await refresh();
                }, "浏览器 Cookie 已导出到本机。")
              }
            >
              {uiText("从浏览器导出")}
            </button>
          </div>
          <p className="help">
            {uiText(
              "导出前请完全退出对应浏览器。若系统加密或权限阻止读取，可改用 Netscape 格式 TXT 文件导入。",
            )}
          </p>
        </div>
      </section>
    </div>
  );
}

function Settings({
  paused,
  config,
  busy,
  action,
  refresh,
  diagnostics,
}: {
  paused: boolean;
  config: Config;
  busy: boolean;
  action: Action;
  refresh: () => Promise<void>;
  diagnostics: any;
}) {
  const [form, setForm] = useState(config);
  useEffect(() => setForm(config), [config]);
  const update = (key: keyof Config, value: unknown) =>
    setForm((old) => ({ ...old, [key]: value }));
  const unsaved = (...keys: (keyof Config)[]) =>
    keys.some((key) => form[key] !== config[key]);
  const supportsAudioLanguage =
    typeof config.youtube_audio_language === "string";
  return (
    <div className="settings-stack">
      <ResourceMonitor paused={paused} />
      <TranslationPanel
        config={config}
        busy={busy}
        action={action}
        refresh={refresh}
      />
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>{uiText("工作目录")}</h2>
            <p>
              {uiText("视频素材可能占用较多空间，建议选择独立的工作文件夹。")}
            </p>
          </div>
          <StatusBadge tone={unsaved("work_dir") ? "error" : "success"}>
            {uiNode(unsaved("work_dir") ? "待保存" : "已保存")}
          </StatusBadge>
        </div>
        <div className="section-body">
          <label className="field">
            {uiText("素材保存位置")}
            <div className="inline-controls">
              <input
                value={form.work_dir}
                onChange={(e) => update("work_dir", e.target.value)}
              />
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    const path = await chooseDirectory();
                    if (path) update("work_dir", path);
                  })
                }
              >
                <FolderOpen size={16} />
                {uiText("选择目录")}
              </button>
            </div>
          </label>
          <p className="help">
            {uiText("更改后只影响新任务；已有素材保持原位。")}
            {diagnostics && (
              <span>
                {" "}
                {uiText("当前可用空间")} {uiNode(bytes(diagnostics.free_bytes))}
              </span>
            )}
          </p>
          <p className="data-path">
            {uiText("任务与设置：")}
            <span data-no-localize>{config.data_dir}</span>
          </p>
        </div>
      </section>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>{uiText("YouTube 下载")}</h2>
            <p>
              {uiText("选择新任务下载源视频时使用的最高分辨率和配音音轨。")}
            </p>
          </div>
          <StatusBadge
            tone={
              unsaved("youtube_max_height", "youtube_audio_language")
                ? "error"
                : "success"
            }
          >
            {uiNode(
              unsaved("youtube_max_height", "youtube_audio_language")
                ? "待保存"
                : "已保存",
            )}
          </StatusBadge>
        </div>
        <div className="section-body">
          <div className="field">
            {uiText("最高分辨率")}
            <StyledSelect
              label="YouTube 下载最高分辨率"
              value={String(form.youtube_max_height)}
              onChange={(value) => update("youtube_max_height", Number(value))}
              options={[
                { value: "0", label: "最佳可用画质" },
                ...[2160, 1440, 1080, 720, 480, 360].map((height) => ({
                  value: String(height),
                  label: `${height}p`,
                })),
              ]}
            />
          </div>
          {supportsAudioLanguage ? (
            <div className="field">
              {uiText("配音音轨")}
              <StyledSelect
                label="YouTube 下载配音音轨"
                value={form.youtube_audio_language}
                onChange={(value) => update("youtube_audio_language", value)}
                options={[
                  { value: "auto", label: "自动（YouTube 默认）" },
                  { value: "original", label: "原声" },
                  { value: "zh", label: "中文" },
                  { value: "en", label: "英语" },
                  { value: "ja", label: "日语" },
                  { value: "ko", label: "韩语" },
                  { value: "es", label: "西班牙语" },
                  { value: "fr", label: "法语" },
                  { value: "de", label: "德语" },
                  { value: "hi", label: "印地语" },
                ]}
              />
            </div>
          ) : (
            <p className="help">
              {uiText(
                "后台尚未加载配音设置。关闭并重新启动桌面程序后即可选择配音。",
              )}
            </p>
          )}
          <p className="help">
            {uiText(
              "分辨率不足时选更低画质；所选配音不存在时优先回退原声，再使用可用音轨。已有任务继续使用创建时的设置。",
            )}
          </p>
        </div>
      </section>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>{uiText("投稿默认值")}</h2>
            <p>
              {uiText("新建任务时保存参数快照，修改默认值不影响已有任务。")}
            </p>
          </div>
          <StatusBadge
            tone={
              unsaved(
                "youtube_category_id",
                "youtube_privacy_status",
                "youtube_made_for_kids",
                "bili_tid",
                "acfun_channel_id",
                "bili_tags",
                "bili_line",
                "upload_gap_seconds",
              )
                ? "error"
                : "success"
            }
          >
            {uiNode(
              unsaved(
                "youtube_category_id",
                "youtube_privacy_status",
                "youtube_made_for_kids",
                "bili_tid",
                "acfun_channel_id",
                "bili_tags",
                "bili_line",
                "upload_gap_seconds",
              )
                ? "待保存"
                : "已保存",
            )}
          </StatusBadge>
        </div>
        <div className="section-body form-grid">
          <label className="field">
            {uiText("投稿分区 ID")}
            <input
              type="number"
              min={1}
              max={65535}
              value={form.bili_tid}
              onChange={(e) => update("bili_tid", Number(e.target.value))}
            />
          </label>
          <AcfunChannelSelect
            label="AcFun 默认分区（自动投稿必填）"
            value={form.acfun_channel_id}
            disabled={busy}
            onChange={(value) => update("acfun_channel_id", value)}
          />
          <YoutubeCategorySelect
            value={form.youtube_category_id || "22"}
            disabled={busy}
            onChange={(value) => update("youtube_category_id", value)}
          />
          <YoutubePrivacySelect
            value={form.youtube_privacy_status || "private"}
            disabled={busy}
            onChange={(value) =>
              update(
                "youtube_privacy_status",
                value as Config["youtube_privacy_status"],
              )
            }
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.youtube_made_for_kids || false}
              disabled={busy}
              onChange={(e) =>
                update("youtube_made_for_kids", e.target.checked)
              }
            />
            {uiText("YouTube 默认面向儿童")}
          </label>
          <div className="field">
            {uiText("上传线路")}
            <StyledSelect
              label="上传线路"
              value={form.bili_line}
              onChange={(value) => update("bili_line", value)}
              options={["tx", "bda2", "qn", "ws", "txa"].map((line) => ({
                value: line,
                label: line,
              }))}
            />
          </div>
          <label className="field">
            {uiText("标签")}
            <input
              value={form.bili_tags}
              onChange={(e) => update("bili_tags", e.target.value)}
              placeholder={uiText("转载,科技")}
            />
          </label>
          <label className="field">
            {uiText("上传间隔（秒）")}
            <input
              type="number"
              min={0}
              max={600}
              value={form.upload_gap_seconds}
              onChange={(e) =>
                update("upload_gap_seconds", Number(e.target.value))
              }
            />
          </label>
        </div>
      </section>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>{uiText("通用设置")}</h2>
            <p>{uiText("选择界面语言与主题，并设置素材校验方式。")}</p>
          </div>
          <StatusBadge
            tone={
              unsaved("ui_language", "theme", "hwaccel", "validation_cache")
                ? "error"
                : "success"
            }
          >
            {uiNode(
              unsaved("ui_language", "theme", "hwaccel", "validation_cache")
                ? "待保存"
                : "已保存",
            )}
          </StatusBadge>
        </div>
        <div className="section-body">
          <div className="setting-row">
            <div>
              <strong>{uiText("界面语言")}</strong>
              <p>{uiText("保存后切换应用的显示语言。")}</p>
            </div>
            <StyledSelect
              label="界面语言"
              value={form.ui_language}
              onChange={(value) => update("ui_language", value)}
              options={[
                { value: "zh-CN", label: "简体中文" },
                { value: "zh-HK", label: "繁體中文" },
                { value: "en", label: "English" },
              ]}
            />
          </div>
          <div className="setting-row">
            <div>
              <strong>{uiText("界面主题")}</strong>
              <p>{uiText("选择浅色、深色或跟随系统。")}</p>
            </div>
            <div className="segmented">
              {[
                { id: "light", icon: Sun, name: "浅色" },
                { id: "dark", icon: Moon, name: "深色" },
                { id: "system", icon: Monitor, name: "系统" },
              ].map((theme) => (
                <button
                  key={theme.id}
                  className={form.theme === theme.id ? "chosen" : ""}
                  onClick={() => update("theme", theme.id)}
                >
                  <theme.icon size={14} />
                  {uiNode(theme.name)}
                </button>
              ))}
            </div>
          </div>
          <div className="setting-row">
            <div>
              <strong>{uiText("完整校验")}</strong>
              <p>
                {uiText(
                  "自动尝试 Intel / AMD / NVIDIA 硬件解码，不可用时回退 CPU。",
                )}
              </p>
            </div>
            <StyledSelect
              label="完整校验"
              value={form.hwaccel}
              onChange={(value) => update("hwaccel", value)}
              options={[
                { value: "auto", label: "自动选择" },
                {
                  value: "d3d11va",
                  label: "D3D11VA（Windows Intel / AMD / NVIDIA）",
                },
                { value: "vaapi", label: "VAAPI（Linux Intel / AMD）" },
                { value: "cuda", label: "CUDA（NVIDIA）" },
                { value: "videotoolbox", label: "VideoToolbox（macOS）" },
                { value: "cpu", label: "仅 CPU" },
              ]}
            />
          </div>
          <label className="setting-row">
            <div>
              <strong>{uiText("复用完整校验缓存")}</strong>
              <p>{uiText("仍检查整文件 SHA-256，命中后跳过重复解码。")}</p>
            </div>
            <input
              type="checkbox"
              checked={form.validation_cache}
              onChange={(e) => update("validation_cache", e.target.checked)}
            />
          </label>
        </div>
      </section>
      <div className="save-row">
        <span className="help">{uiText("任务运行期间不能修改全局设置。")}</span>
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            action(async () => {
              const {
                work_dir,
                bili_tid,
                acfun_channel_id,
                youtube_category_id,
                youtube_privacy_status,
                youtube_made_for_kids,
                bili_tags,
                bili_line,
                youtube_max_height,
                youtube_audio_language,
                upload_gap_seconds,
                theme,
                ui_language,
                hwaccel,
                validation_cache,
              } = form;
              await request("settings.update", {
                values: {
                  work_dir,
                  bili_tid,
                  acfun_channel_id,
                  youtube_category_id,
                  youtube_privacy_status,
                  youtube_made_for_kids,
                  bili_tags,
                  bili_line,
                  youtube_max_height,
                  ...(supportsAudioLanguage ? { youtube_audio_language } : {}),
                  upload_gap_seconds,
                  theme,
                  ui_language,
                  hwaccel,
                  validation_cache,
                },
              });
              await refresh();
            }, "设置已保存。")
          }
        >
          <Check size={16} />
          {uiText("保存设置")}
        </button>
      </div>
    </div>
  );
}
