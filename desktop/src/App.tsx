import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  FileText,
  FolderOpen,
  History,
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
  subscribe,
  preview,
  chooseFile,
  chooseDirectory,
  saveLog,
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
  retryable,
  transferRate,
  type Config,
  type Progress,
  type Task,
} from "./types";
import SetupWizard from "./SetupWizard";
import { AccountsPanel, AccountSelector, QueueOverview } from "./Accounts";
import { DouyinAccountPanel, PublicationDetails } from "./Douyin";
import { AcfunAccountPanel, AcfunChannelSelect } from "./Acfun";
import { accountLabel, type BiliAccount } from "./types";
import TranslationPanel from "./TranslationPanel";
import { StyledSelect } from "./StyledSelect";
import { StatusBadge } from "./StatusBadge";
import { SubmissionTrend, type SubmissionTrendData } from "./SubmissionTrend";
import { ToastViewport, ReportError, useToast } from "./Toast";
import { setUiLanguage, uiText } from "./i18n";

type Page = "tasks" | "history" | "account" | "settings";
const PAGE_SIZE = 20;
type ShutdownStatus = {
  ready: boolean;
  pending_task_ids: string[];
  inflight_task_ids: string[];
};
const titles = {
  tasks: "任务中心",
  history: "投稿记录",
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
        close();
      }}
    >
      <div className="modal-head">
        <h2 id={titleId}>{title}</h2>
        <button className="icon-button" aria-label="关闭对话框" onClick={close}>
          <X size={19} />
        </button>
      </div>
      {children}
      <ToastViewport />
    </dialog>
  );
}

function Status({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <span />
      {labels[status] || status}
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
      setHeight((current) => current === null || Math.abs(current - next) > 1 ? next : current);
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
  const { showError, showSuccess } = useToast();
  const [page, setPage] = useState<Page>("tasks");
  const [config, setConfig] = useState<Config | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loadedListKey, setLoadedListKey] = useState("");
  const [total, setTotal] = useState(0);
  const [allTotal, setAllTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [todaySubmittedByAccount, setTodaySubmittedByAccount] =
    useState<Record<string, number>>({});
  const [submissionTrend, setSubmissionTrend] = useState<SubmissionTrendData | null>(null);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [connected, setConnected] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [newTask, setNewTask] = useState(false);
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
  const [accountFilter, setAccountFilter] = useState("");
  const [accountOptions, setAccountOptions] = useState<BiliAccount[]>([]);
  const [shutdownStarted, setShutdownStarted] = useState(false);
  const [shutdownStatus, setShutdownStatus] = useState<ShutdownStatus | null>(null);
  const taskRequest = useRef(0);
  const listKey = JSON.stringify([page, accountFilter, query, filter, offset]);
  const taskRowsByKey = useRef(new Map<string, number>());
  const listLoading = (page === "tasks" || page === "history") && loadedListKey !== listKey;
  const historyLoading = page === "history" && listLoading;
  const taskOverviewLoading = page === "tasks" && !loadedListKey.startsWith('["tasks",');
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
  const loadConfig = useCallback(
    async () => {
      const value: Config = await request("settings.get");
      setUiLanguage(value.ui_language ?? "zh-CN");
      setConfig(value);
    },
    [],
  );
  const loadTasks = useCallback(async () => {
    const generation = ++taskRequest.current;
    const result = await request("tasks.list", {
      account_id: accountFilter,
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
    if (page === "tasks") taskRowsByKey.current.set(listKey, result.items.length);
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
  }, [query, filter, offset, page, accountFilter, listKey]);
  const refreshAccount = useCallback(async () => {
    setAuth(await request("auth.status"));
    setAccountOptions((await request("accounts.list")).items);
  }, []);

  useEffect(() => {
    let alive = true;
    request("system.health")
      .then(async (result) => {
        if (result.protocol_version !== 2)
          throw new Error("桌面与后台协议版本不匹配。");
        await Promise.all([loadConfig(), refreshAccount()]);
        if (alive) {
          setConnected(true);
          setConnectionError("");
        }
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
          ...(event.payload.publication_id ? { [event.payload.publication_id]: event.payload } : {}),
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
      stop();
      stopClose();
    };
  }, [loadConfig, refreshAccount, showSuccess]);

  useEffect(() => {
    if (!connected) return;
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
  }, [connected, loadTasks]);

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
      document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.setAttribute(
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
        if (connected) setNewTask(true);
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, [connected]);

  useEffect(() => {
    if (!shutdownStarted) return;
    let stop = false;
    const tick = async () => {
      try {
        const state = (await request("system.shutdown_status")) as ShutdownStatus;
        if (!stop) setShutdownStatus(state);
        if (state.ready && !stop) {
          await closeApp();
          return;
        }
      } catch (e) {
        showError(String(e));
      }
      if (!stop) timer = setTimeout(tick, 500);
    };
    let timer = setTimeout(tick, 0);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [shutdownStarted, showError]);
  useEffect(() => {
    if (connectionError) showError(`未连接到后台：${connectionError}`, {
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
  const exportLogs = () =>
    action(async () => {
      const path = await saveLog();
      if (path) {
        await request("logs.export", { path });
        showSuccess("诊断日志已导出。");
      }
    });
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
    { id: "history", icon: History },
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
              yt2bili<small>by StarDazz</small>
            </span>
          </a>
          <div className="nav-label">工作空间</div>
          <nav aria-label="主导航">
            {nav.map((item) => (
              <button
                key={item.id}
                className={`nav-item ${page === item.id ? "selected" : ""}`}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={18} />
                {titles[item.id]}
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
                在你的设备上运行<small>素材处理在本机 · 按平台授权投稿</small>
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
                title="切换明暗主题"
                aria-label="切换明暗主题"
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
              工作空间 <span>/</span> <strong>{titles[page]}</strong>
            </div>
            <div className="connection">
              <i className={connected ? "online" : ""} />
              {preview
                ? "界面预览 · 示例数据"
                : connected
                  ? "本地服务已连接"
                  : "正在连接本地服务"}
            </div>
          </header>
          <main>
            <div className="page-heading">
              <div>
                <span className="eyebrow">
                  {page === "tasks"
                    ? "YOUR LOCAL WORKFLOW"
                    : page === "history"
                      ? "PUBLISHING HISTORY"
                      : page === "account"
                        ? "CONNECTED SERVICES"
                        : "MAKE IT YOURS"}
                </span>
                <h1>{titles[page]}</h1>
                <p>
                  {page === "tasks"
                    ? "从链接到投稿，每一步都清晰可见。"
                    : page === "history"
                      ? "所有已提交稿件，以及需要你核对的结果。"
                      : page === "account"
                        ? "连接你的账号，开始本地创作流程。"
                        : "按你的习惯设置工作目录与投稿默认值。"}
                </p>
              </div>
              {page === "tasks" && (
                <button
                  className="primary"
                  onClick={() => setNewTask(true)}
                  disabled={!connected}
                >
                  <Plus size={17} />
                  新建任务 <kbd>⌘ / Ctrl N</kbd>
                </button>
              )}
              {page === "history" && (
                <div className="button-row">
                  <button
                    className="secondary"
                    disabled={!connected || preview || busy}
                    onClick={() => void action(async () => {
                      const path = await chooseFile(["json"]);
                      if (!path) return;
                      const result = await request("history.import", { path });
                      await loadTasks();
                      setFilter("");
                      setQuery("");
                      setAccountFilter("");
                      setOffset(0);
                      showSuccess(`导入 ${result.imported} 条记录，跳过 ${result.skipped} 条已有记录。`);
                    })}
                  >
                    <Upload size={16} /> 导入投稿记录
                  </button>
                  <button
                    className="secondary"
                    disabled={!connected || preview || busy}
                    onClick={() => void action(async () => {
                      const path = await saveHistory();
                      if (!path) return;
                      const result = await request("history.export", { path });
                      showSuccess(`已导出 ${result.exported} 条投稿记录。`);
                    })}
                  >
                    <ArrowDownToLine size={16} /> 导出投稿记录
                  </button>
                  <button
                    className="secondary danger"
                    disabled={!connected || busy || historyLoading || allTotal === 0}
                    onClick={() => setConfirmDeleteAllHistory(true)}
                  >
                    <Trash2 size={16} /> 删除所有投稿记录
                  </button>
                </div>
              )}
            </div>
            {(page === "tasks" || page === "history") && (
              <>
                {page === "tasks" && (
                  <div>
                    <div className="stats-heading">B 站账号 · 今日提交成功</div>
                    <div
                      className="stats-strip"
                      style={{
                        gridTemplateColumns: `repeat(${Math.max(activeAccounts.length, 1)}, minmax(0, 1fr))`,
                      }}
                    >
                      {taskOverviewLoading ? Array.from({ length: Math.max(activeAccounts.length, 1) }, (_, index) => (
                        <div className="stat stat-skeleton" key={index} aria-hidden="true">
                          <span className="stat-icon skeleton-block" />
                          <div>
                            <span className="skeleton-block skeleton-stat-label" />
                            <strong className="skeleton-block skeleton-stat-value" />
                          </div>
                        </div>
                      )) : activeAccounts.length === 0 && (
                        <div className="stat stat-empty">
                          连接 B 站账号后，这里会显示各账号今日提交成功数。
                        </div>
                      )}
                      {!taskOverviewLoading && activeAccounts.map((account) => (
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
                              {account.remark || account.nickname || `UID ${account.uid}`}
                            </span>
                            <strong>
                              {String(todaySubmittedByAccount[account.account_id] || 0).padStart(2, "0")}
                            </strong>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {config && !config.translation_ready && page === "tasks" && (
                  <div className="setup-banner">
                    <div className="setup-icon">
                      <Zap size={20} />
                    </div>
                    <div>
                      <strong>先完成一次简单的配置</strong>
                      <p>
                        配置本地翻译或 DeepL 并连接 B
                        站账号，之后就可以在这里管理任务。
                      </p>
                    </div>
                    <button
                      className="secondary"
                      onClick={() => setSetup(true)}
                    >
                      开始配置 <ArrowRight size={15} />
                    </button>
                  </div>
                )}
                {page === "history"
                  ? historyLoading
                    ? <section className="submission-trend history-trend-skeleton" aria-label="正在加载投稿记录" role="status">
                        <div className="submission-trend-heading" aria-hidden="true">
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
                        <div className="submission-trend-legend" aria-hidden="true">
                          {activeAccounts.map((account) => <span className="skeleton-block skeleton-trend-legend" key={account.account_id} />)}
                        </div>
                      </section>
                    : <SubmissionTrend trend={submissionTrend} accounts={accountOptions} />
                  : taskOverviewLoading
                    ? <div className="queue-overview queue-overview-skeleton" role="status" aria-label="正在加载任务">
                        {Array.from({ length: 5 + activeAccounts.length }, (_, index) => (
                          <div className="queue-card" key={index} aria-hidden="true">
                            <strong className="skeleton-block skeleton-queue-title" />
                            <small className="skeleton-block skeleton-queue-detail" />
                          </div>
                        ))}
                      </div>
                    : <QueueOverview queue={queue} accounts={auth.accounts || []} />}
                <div className="field account-filter">
                  按账号筛选
                  <StyledSelect
                    label="按账号筛选"
                    value={accountFilter}
                    onChange={(value) => {
                      setAccountFilter(value);
                      setOffset(0);
                    }}
                    options={[{ value: "", label: "全部账号" }, ...accountOptions.map((a) => ({ value: a.account_id, label: accountLabel(a) }))]}
                  />
                </div>
                <div className="task-panel">
                  <div className="panel-toolbar">
                    <div className="tabs">
                      <button
                        className={!filter ? "current" : ""}
                        onClick={() => {
                          setFilter("");
                          setOffset(0);
                        }}
                      >
                        {page === "history" ? "全部记录" : "全部任务"} <span>{listLoading ? "…" : allTotal}</span>
                      </button>
                      {(page === "history"
                        ? [
                            "submitted",
                            "submission_unknown",
                            "partial_success",
                            "completed_with_abandon",
                          ]
                        : ["ready", "failed"]
                      ).map((status) => (
                        <button
                          key={status}
                          className={filter === status ? "current" : ""}
                          onClick={() => {
                            setFilter(status);
                            setOffset(0);
                          }}
                        >
                          {labels[status]}
                        </button>
                      ))}
                    </div>
                    <label className="search">
                      <Search size={16} />
                      <input
                        aria-label="搜索任务"
                        placeholder="搜索标题或视频 ID"
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value);
                          setOffset(0);
                        }}
                      />
                    </label>
                  </div>
                  <div className="table-head">
                    <span>视频 / 标题</span>
                    <span>当前状态</span>
                    <span>最近更新</span>
                    <span />
                  </div>
                  <ResizingList key={page}>
                  {listLoading && page === "tasks" && taskRowsByKey.current.get(listKey) === 0 ? (
                    <div className="empty-state empty-state-skeleton" role="status" aria-label="正在加载任务">
                      <span className="skeleton-block skeleton-empty-art" aria-hidden="true" />
                      <span className="skeleton-block skeleton-empty-heading" aria-hidden="true" />
                      <span className="skeleton-block skeleton-empty-copy" aria-hidden="true" />
                    </div>
                  ) : listLoading ? (
                    <div className={`task-rows task-rows-skeleton ${page}-rows-skeleton`} role="status" aria-label={page === "history" ? "正在加载投稿记录" : "正在加载任务"}>
                      {Array.from({ length: page === "tasks" ? taskRowsByKey.current.get(listKey) ?? 3 : 5 }, (_, index) => (
                        <div className="task-row" key={index} aria-hidden="true">
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
                      ))}
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
                              <Video size={20} />
                            </span>
                            <span>
                              <strong>
                                {task.title_zh || task.title_orig
                                  ? <span data-no-localize>{task.title_zh || task.title_orig}</span>
                                  : "等待读取视频信息"}
                              </strong>
                              <small>
                                {task.video_id} ·{" "}
                                {task.account_name_snapshot || "历史账号待确认"}
                                {task.account_uid_snapshot
                                  ? ` (UID ${task.account_uid_snapshot})`
                                  : ""}
                                {task.imported_history && " · 导入记录"}
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
                                    {progressSpeed(progress[task.task_id])}
                                  </small>
                                )}
                              </>
                            )}
                          </span>
                          <time>
                            {new Date(
                              task.updated_at || task.created_at,
                            ).toLocaleString("zh-CN", {
                              month: "2-digit",
                              day: "2-digit",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
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
                        {query || filter
                          ? "没有找到匹配的任务"
                          : page === "history"
                            ? "还没有投稿记录"
                            : "你的下一条视频，从这里开始"}
                      </h2>
                      <p>
                        {query || filter
                          ? "试试其他关键词，或查看全部任务。"
                          : page === "history"
                            ? "完成投稿后，BV 号和提交记录会保留在这里。"
                            : "粘贴 YouTube 链接，我们会为你下载、校验并翻译素材。"}
                      </p>
                      {!query && !filter && page === "tasks" && (
                        <button
                          className="secondary"
                          onClick={() => setNewTask(true)}
                          disabled={!connected}
                        >
                          <Plus size={16} />
                          添加第一个任务
                        </button>
                      )}
                      <div className="workflow-hint">
                        <span>01 下载</span>
                        <ArrowRight size={12} />
                        <span>02 校验</span>
                        <ArrowRight size={12} />
                        <span>03 预览与投稿</span>
                      </div>
                    </div>
                  )}
                  </ResizingList>
                  <div className="panel-footer">
                    <span>
                      {page === "tasks"
                        ? "默认先准备素材，由你确认后投稿"
                        : "已提交不代表通过平台审核"}
                    </span>
                    <div>
                      <span>{listLoading ? "…" : `${total} 条记录`}</span>
                      <button
                        aria-label="上一页"
                        className="icon-button"
                        disabled={listLoading || !offset}
                        onClick={() => setOffset((v) => Math.max(0, v - PAGE_SIZE))}
                      >
                        <ChevronLeft size={15} />
                      </button>
                      <button
                        aria-label="下一页"
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
                  仅处理你拥有版权或已获授权的视频。下载、校验、上传各自排队，互不阻塞。
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
                config={config}
                busy={busy}
                action={action}
                refresh={loadConfig}
                diagnostics={diagnostics}
                setDiagnostics={setDiagnostics}
                exportLogs={exportLogs}
              />
            )}
          </main>
          <footer className="bottom-bar">
            <span>
              <Activity size={12} />
              {queueActive.length
                ? `${queueActive.length} 个任务正在队列中`
                : "所有队列空闲"}
            </span>
            <span>本地处理 · 由你掌控</span>
          </footer>
        </div>
        {setup && config && (
          <Modal
            title="欢迎使用 yt2bili"
            close={() => {
              if (!busy)
                action(async () => {
                  await request("auth.login.cancel");
                  setSetup(false);
                });
            }}
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
          </Modal>
        )}
        {confirmDeleteAllHistory && (
          <Modal title="删除所有投稿记录" close={() => { if (!busy) setConfirmDeleteAllHistory(false); }}>
            <p className="modal-copy">
              确定删除本机全部投稿记录？包括已提交、待核对和导入的记录。平台上的稿件、本机素材及未完成的任务不会删除；记录删除后只能从备份或导出的文件恢复。
            </p>
            <div className="modal-actions">
              <button className="secondary" disabled={busy} onClick={() => setConfirmDeleteAllHistory(false)}>取消</button>
              <button className="secondary danger" disabled={busy} onClick={() => void action(async () => {
                await request("history.delete_all", { confirmed: true });
                setConfirmDeleteAllHistory(false);
                latestTasks.current = {};
                setOffset(0);
                await loadTasks();
                showSuccess("投稿记录已全部删除。");
              })}>确认删除所有投稿记录</button>
            </div>
          </Modal>
        )}
        {newTask && (
          <NewTask
            accounts={auth.accounts || []}
            config={config}
            busy={busy}
            action={action}
            close={() => {
              if (!busy) setNewTask(false);
            }}
            done={async () => {
              setNewTask(false);
              await loadTasks();
            }}
          />
        )}
        {selected && (selected.imported_history ? (
          <ImportedHistoryDetail task={selected} busy={busy} onDelete={deleteHistory} close={() => setSelected(null)} />
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
              const updated = await request<Task>("tasks.get", { task_id: taskId });
              setSelected((current) =>
                current?.task_id === taskId ? updated : current,
              );
            }}
          />
        ))}
        {closing && (
          <Modal
            title="退出 yt2bili"
            close={() => {
              if (!shutdownStarted) setClosing(false);
            }}
          >
            <p className="modal-copy">
              {shutdownStarted
                ? `已停止未投稿任务并保留素材。仍有 ${shutdownStatus?.inflight_task_ids.length ?? 0} 个在途投稿、${shutdownStatus?.pending_task_ids.length ?? 0} 个任务待收尾；投稿结束后自动退出。`
                : "退出会取消未投稿任务，并等待正在投稿的账号完成。"}
            </p>
            {shutdownStarted && (
              <p className="help">
                如需立即退出，可停止在途投稿。素材会保留；投稿请求可能已到达平台，重启后请核对结果，再决定是否重试。
              </p>
            )}
            <div className="modal-actions">
              {!shutdownStarted && (
                <button className="secondary" onClick={() => setClosing(false)}>
                  继续使用
                </button>
              )}
              {shutdownStarted && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => action(async () => { await forceCloseApp(); })}
                >
                  停止投稿并退出
                </button>
              )}
              <button
                disabled={shutdownStarted || busy}
                className="primary"
                onClick={() =>
                  action(async () => {
                    const state = (await request("system.prepare_shutdown")) as ShutdownStatus;
                    setShutdownStatus(state);
                    setShutdownStarted(true);
                  })
                }
              >
                {shutdownStarted ? "等待安全退出…" : "退出应用"}
              </button>
            </div>
          </Modal>
        )}
      </div>
  );
}

type Action = (work: () => Promise<unknown>, success?: string) => Promise<void>;
function NewTask({
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
  const [accountId, setAccountId] = useState("");
  const [text, setText] = useState("");
  const [mode, setMode] = useState("preview");
  const [authorized, setAuthorized] = useState(false);
  const [douyin, setDouyin] = useState<any>(null);
  const [syncDouyin, setSyncDouyin] = useState(false);
  const [acfun, setAcfun] = useState<any>(null);
  const [syncAcfun, setSyncAcfun] = useState(false);
  useEffect(() => {
    let alive = true;
    request("douyin.auth.status")
      .then((status) => status.account
        ? request("douyin.auth.status", { verify: true })
        : status)
      .then((s) => {
        if (alive) setDouyin(s);
      })
      .catch(() => {});
    request("acfun.auth.status", { verify: true })
      .then((s) => { if (alive) setAcfun(s); })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const op = useRef(operationId());
  return (
    <Modal title="新建任务" close={close}>
      <p className="modal-copy">每次添加一个视频，并选择本次投稿的账号。</p>
      <label className="field">
        YouTube 视频链接
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
      <AccountSelector
        accounts={accounts}
        value={accountId}
        onChange={(id) => {
          setAccountId(id);
          op.current = operationId();
        }}
      />
      {!accounts.length && (
        <p className="help">请先到账号与连接添加 Bilibili 账号。</p>
      )}
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
              准备素材并预览 <em>推荐</em>
            </strong>
            <small>下载、校验和翻译完成后，由你确认投稿。</small>
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
            <strong>自动投稿</strong>
            <small>素材准备好后，自动提交到本次选择的平台。</small>
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
        同步上传抖音{douyin?.account ? ` · ${douyin.account.nickname}` : ""}
      </label>
      {!douyin?.can_sync && (
        <p className="help">
          请先在“账号与连接”登录抖音，登录后才能勾选。
        </p>
      )}
      {syncDouyin && (
        <p className="help">
          共用素材，两平台独立排队。预览模式一起确认；自动模式分别自动投稿。
        </p>
      )}
      <ReportError message={syncDouyin && mode === "auto" && !douyin?.capabilities?.auto_publish
        ? "当前抖音服务未启用官方批准的自动发布能力，请选择预览模式。" : null} />
      <label className="checkbox">
        <input type="checkbox" checked={syncAcfun} disabled={!acfun?.can_sync}
          onChange={(e) => { setSyncAcfun(e.target.checked); op.current = operationId(); }} />
        同步上传 AcFun{acfun?.account ? ` · ${acfun.account.nickname}` : ""}
      </label>
      {!acfun?.can_sync && <p className="help">
        {acfun?.account?.auth_state === "valid"
          ? "AcFun 账号已登录，但后台仍使用旧版配置。请使用最新构建，完整退出并重新打开应用后重试。"
          : acfun?.account
            ? "AcFun 登录已失效，请在“账号与连接”重新扫码。"
            : "请先在“账号与连接”扫码登录 AcFun。"}
      </p>}
      {acfun?.account?.auth_state === "valid" && <p className="help">AcFun 使用独立队列，并共用本任务的标签与简介；标题单独翻译且最多 50 字。{mode === "auto" ? "素材准备好后自动投稿。" : "预览后确认投稿。"}</p>}
      <ReportError message={syncAcfun && mode === "auto" && !config?.acfun_channel_id
        ? "请先在设置中填写 AcFun 默认分区 ID，再创建自动投稿任务。" : null} />
      <label className="checkbox">
        <input
          type="checkbox"
          checked={authorized}
          onChange={(e) => setAuthorized(e.target.checked)}
        />
        我拥有该视频的版权或已获得转载授权。
      </label>
      </div>
      <div className="modal-actions">
        <button className="secondary" onClick={close} disabled={busy}>
          取消
        </button>
        <button
          className="primary"
          disabled={
            busy ||
            !text.trim() ||
            !authorized ||
            !accountId ||
            (syncDouyin &&
              mode === "auto" &&
              !douyin?.capabilities?.auto_publish) ||
            (syncAcfun && mode === "auto" && (!acfun?.capabilities?.auto_publish || !config?.acfun_channel_id))
          }
          onClick={() =>
            action(async () => {
              const result = await request("tasks.create", {
                url: text,
                account_id: accountId,
                mode,
                ...(syncDouyin
                  ? {
                      sync_douyin: true,
                      douyin_account_id: douyin.account.account_id,
                      douyin_binding_revision: douyin.account.binding_revision,
                    }
                  : {}),
                ...(syncAcfun ? {
                  sync_acfun: true,
                  acfun_account_id: acfun.account.account_id,
                  acfun_binding_revision: acfun.account.binding_revision,
                } : {}),
                operation_id: op.current,
              });
              if (!result.created)
                throw new Error("该账号已有此视频任务，请在列表中查看或继续。");
              await done();
            }, "任务已加入队列。")
          }
        >
          {busy ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
          加入队列
        </button>
      </div>
    </Modal>
  );
}

function ImportedHistoryDetail({ task, busy, onDelete, close }: {
  task: Task;
  busy: boolean;
  onDelete: (task: Task) => Promise<void>;
  close: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const platforms = { bilibili: "Bilibili", douyin: "抖音", acfun: "AcFun" };
  return (
    <Modal title="导入的投稿记录" close={close} wide>
      <div className="section-body history-detail">
        <p className="help">这是一条只读历史记录，不包含登录凭据或视频素材，也不会加入投稿队列。</p>
        <h3 data-no-localize>{task.title_zh || task.title_orig || task.video_id}</h3>
        <p><Status status={task.status} /> · {task.video_id}</p>
        <p>账号：{task.account_name_snapshot || "原账号"}{task.account_uid_snapshot ? ` · UID ${task.account_uid_snapshot}` : ""}</p>
        <p>创建：{task.created_at || "未知"} · 更新：{task.updated_at || "未知"}</p>
        {task.url && <p>来源：{task.url}</p>}
        {task.publications?.map((pub) => (
          <div className="queue-card" key={pub.platform}>
            <strong>{platforms[pub.platform]} · {labels[pub.status] || pub.status}</strong>
            {pub.account_label && <p>投稿账号：{pub.account_label}</p>}
            {pub.remote_id && <p>稿件号：{pub.remote_id}</p>}
            {pub.text && <p>投稿标题：<span data-no-localize>{pub.text}</span></p>}
            {pub.error && <p>备注：<span data-no-localize>{pub.error}</span></p>}
          </div>
        ))}
        {task.desc_zh && <div><strong>中文简介</strong><pre data-no-localize>{task.desc_zh}</pre></div>}
        {task.desc_orig && <div><strong>原始简介</strong><pre data-no-localize>{task.desc_orig}</pre></div>}
        {confirmDelete && <div className="confirm-box">
          <strong>确定从本机删除这条导入记录？不会删除平台上的稿件；重新导入原文件可以恢复。</strong>
          <div>
            <button className="secondary" onClick={() => setConfirmDelete(false)}>返回</button>
            <button className="secondary danger" disabled={busy} onClick={() => void onDelete(task)}>确认删除记录</button>
          </div>
        </div>}
      </div>
      <div className="modal-actions">
        <button className="secondary danger" disabled={busy} onClick={() => setConfirmDelete(true)}>删除投稿记录</button>
        <button className="secondary" onClick={close}>关闭</button>
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
  const [logs, setLogs] = useState<any[]>([]);
  const [confirm, setConfirm] = useState("");
  const [bv, setBv] = useState("");
  const [douyinDirty, setDouyinDirty] = useState(false);
  const descriptionLimit = task.publications?.some((p) => p.platform === "acfun" && p.status === "ready") ? 1000 : 2000;
  const op = useRef(operationId());
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
    <Modal title="任务详情" close={close} wide>
      <div className="detail-summary">
        {cover ? (
          <img className="cover" src={cover} alt="视频封面" />
        ) : (
          <div className="cover placeholder">
            <Video size={35} />
          </div>
        )}
        <div>
          <Status status={task.status} />
          <h3 data-no-localize>{task.title_zh || task.title_orig || task.video_id}</h3>
          <p>
            {task.uploader || "等待读取作者"} · {task.video_id}
            <br />
            {task.account_name_snapshot || "历史账号待确认"}
            {task.account_uid_snapshot
              ? ` · UID ${task.account_uid_snapshot}`
              : ""}
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
            打开素材目录
          </button>
          {task.bv_id && (
            <button
              className="text-button"
              onClick={() =>
                external(`https://www.bilibili.com/video/${task.bv_id}`)
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
            {labels[progress?.stage || task.status]}
            {progress?.provider
              ? ` · ${progress.provider === "local_llm" ? "本地大模型" : "DeepL"}`
              : ""}
            {progress?.track && ` · ${progress.track} · ${progress.backend}`}{" "}
            {progressSpeed(progress) && `· ${progressSpeed(progress)} `}
            {progress?.remaining
              ? `· 剩余 ${Math.ceil(progress.remaining)} 秒`
              : ""}
          </span>
          <strong>
            {progress?.percent != null
              ? `${progress.percent.toFixed(1)}%`
              : "进行中"}
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
          <p>历史账号待确认：绑定后不可改投，请核对原投稿归属。</p>
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
            确认历史账号
          </button>
        </div>
      )}
      {task.translation?.state === "queued" && (
        <p className="help">旧译文已清空，{task.status === "translating" ? "正在重新翻译。" : "等待重新翻译。"}</p>
      )}
      {task.translation && !["queued", "failed", "cancelled"].includes(task.translation.state || "") && (
        <p className="help">
          翻译来源：
          {
            (
              {
                local_llm: "本地大模型",
                deepl: "DeepL",
                none: "无需翻译",
                unknown: "历史译文",
              } as Record<string, string>
            )[task.translation.provider || "unknown"]
          }
          {task.translation.fallback_used &&
            ` · 已切换服务（${task.translation.fallback_reason}）`}
          {task.translation.elapsed_ms != null &&
            ` · ${(task.translation.elapsed_ms / 1000).toFixed(1)} 秒`}
          {task.translation.user_edited && " · 已人工修改"}
          {task.translation.input_truncated && " · 原文片段已截短"}
        </p>
      )}
      <div className="tabs detail-tabs">
        <button
          className={tab === "metadata" ? "current" : ""}
          onClick={() => setTab("metadata")}
        >
          投稿素材
        </button>
        <button
          className={tab === "original" ? "current" : ""}
          onClick={() => setTab("original")}
        >
          原始信息
        </button>
        <button
          className={tab === "logs" ? "current" : ""}
          onClick={() => setTab("logs")}
        >
          运行日志
        </button>
      </div>
      {tab === "metadata" && (
        <div className="detail-form">
          <label className="field">
            中文标题 <span>{title.length} / 80</span>
            <input
              value={title}
              maxLength={80}
              readOnly={!editable(task)}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="field">
            简介 <span>{description.length} / {descriptionLimit}</span>
            <textarea
              rows={7}
              value={description}
              maxLength={descriptionLimit}
              readOnly={!editable(task)}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <p className="help">
            简介中的原标题、作者与来源链接会保留。获得 BV
            号后，任务素材将自动清理。
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
                      title,
                      description,
                    });
                    await refresh();
                  }, "投稿信息已保存。")
                }
              >
                <Check size={15} />
                保存修改
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => setConfirm("retranslate")}
              >
                重新翻译
              </button>
            </div>
          )}
        </div>
      )}
      {tab === "original" && (
        <div className="original-info">
          <h3>{task.title_orig ? <span data-no-localize>{task.title_orig}</span> : "尚未读取"}</h3>
          <p>{task.url}</p>
          <pre>{task.desc_orig ? <span data-no-localize>{task.desc_orig}</span> : "暂无原始简介"}</pre>
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
            <p>本次会话暂无该任务日志。</p>
          )}
        </div>
      )}
      {task.status === "submission_unknown" &&
        !task.publications?.some((p) => p.platform !== "bilibili") && (
          <div className="resolve-box">
            <strong>先核对创作中心，再继续处理</strong>
            <p>网络中断不一定代表投稿失败。登记 BV 号会保留本地素材。</p>
            <button
              className="text-button"
              onClick={() =>
                external(
                  "https://member.bilibili.com/platform/upload-manager/article",
                )
              }
            >
              打开创作中心 <ExternalLink size={14} />
            </button>
            <div className="inline-controls">
              <input
                aria-label="登记 BV 号"
                value={bv}
                onChange={(e) => setBv(e.target.value)}
                placeholder="BV…"
              />
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    await request("tasks.resolve", {
                      task_id: task.task_id,
                      bv_id: bv,
                    });
                    await refresh();
                  }, "BV 号已登记。")
                }
              >
                登记已提交稿件
              </button>
            </div>
            <button
              className="text-button"
              onClick={() => setConfirm("resolve")}
            >
              我已核对，确实没有提交
            </button>
          </div>
        )}
      {retryable(task) && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() =>
            void action(async () => {
              await request("tasks.retry", {
                task_id: task.task_id,
                operation_id: operationId(),
                use_current_translation_settings: true,
              });
              await refresh();
            }, "已使用当前翻译设置继续准备素材。")
          }
        >
          使用当前翻译设置重试
        </button>
      )}
      {confirm && (
        <div className="confirm-box">
          <strong>
            {confirm === "retranslate"
              ? "将立即清空当前中文标题和简介（含人工修改），按当前设置重新进入翻译队列。若翻译失败，旧译文不会恢复；完成后需重新预览。确定继续？"
              : confirm === "submit"
                ? `确认投稿到 ${task.account_name_snapshot} · UID ${task.account_uid_snapshot}${task.publications?.some((p) => p.platform === "douyin") ? "，并同步到抖音" : ""}${task.publications?.some((p) => p.platform === "acfun") ? "，并同步到 AcFun" : ""}？`
                : confirm === "repair"
                  ? "准备原稿件的替换视频？此操作不会投稿。"
                  : confirm === "delete_history"
                    ? "确定从本机删除这条任务及其所有平台投稿记录？不会删除平台上的稿件。未投稿成功的任务会一并删除本机素材；已提交或待核对的任务会保留素材。删除后只能从备份恢复记录。"
                  : "确认创作中心没有这条稿件？"}
          </strong>
          <div>
            <button className="secondary" onClick={() => setConfirm("")}>
              返回
            </button>
            <button
              className={confirm === "delete_history" ? "secondary danger" : "primary"}
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
              {confirm === "delete_history" ? "确认删除记录" : "确认"}
            </button>
          </div>
        </div>
      )}
      <div className="modal-actions">
        <span className="help">已提交 ≠ 已过审</span>
        {!active(task) && (
          <button className="secondary danger" disabled={busy} onClick={() => setConfirm("delete_history")}>
            删除投稿记录
          </button>
        )}
        <button className="secondary" onClick={close}>
          关闭
        </button>
        {retryable(task) && task.account_id && (
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              command("tasks.retry", "已继续准备素材，完成后等待预览。")
            }
          >
            <RefreshCw size={15} />
            继续任务
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
              douyinDirty
            }
            title="请先保存修改"
            onClick={() => setConfirm("submit")}
          >
            <Send size={15} />
            确认投稿
          </button>
        )}
        {task.status === "submitted" && task.bv_id && (
          <button
            className="secondary"
            disabled={busy}
            onClick={() => setConfirm("repair")}
          >
            准备修复素材
          </button>
        )}
        {active(task) && task.status !== "uploading" && (
          <button
            className="secondary danger"
            disabled={busy || task.status === "cancel_requested"}
            onClick={() =>
              action(async () => {
                const result = await request<{ status: string }>("tasks.cancel", { task_id: task.task_id });
                await refresh();
                showSuccess(result.status === "cancelled" ? "任务已取消，记录已保留。" : "已请求取消，素材会保留。");
              })
            }
          >
            取消任务
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
            <h2>YouTube 访问</h2>
            <p>当源站要求登录验证时，提供你的浏览器 Cookie。</p>
          </div>
          <StatusBadge tone={config.youtube_cookies ? "success" : "error"}>
            {config.youtube_cookies ? "已导入" : "未导入"}
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
              导入 Cookie 文件
            </button>
            <StyledSelect
              label="浏览器"
              value={browser}
              onChange={setBrowser}
              options={[{ value: "edge", label: "Edge" }, { value: "chrome", label: "Chrome" }, { value: "firefox", label: "Firefox" }]}
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
              从浏览器导出
            </button>
          </div>
          <p className="help">
            导出前请完全退出对应浏览器。若系统加密或权限阻止读取，可改用
            Netscape 格式 TXT 文件导入。
          </p>
        </div>
      </section>
    </div>
  );
}

function Settings({
  config,
  busy,
  action,
  refresh,
  diagnostics,
  setDiagnostics,
  exportLogs,
}: {
  config: Config;
  busy: boolean;
  action: Action;
  refresh: () => Promise<void>;
  diagnostics: any;
  setDiagnostics: (v: any) => void;
  exportLogs: () => void;
}) {
  const [form, setForm] = useState(config);
  const [importConfirm, setImportConfirm] = useState(false);
  useEffect(() => setForm(config), [config]);
  const update = (key: keyof Config, value: unknown) =>
    setForm((old) => ({ ...old, [key]: value }));
  const unsaved = (...keys: (keyof Config)[]) => keys.some((key) => form[key] !== config[key]);
  const supportsAudioLanguage = typeof config.youtube_audio_language === "string";
  const toolsReady = diagnostics &&
    ["ffmpeg", "ffprobe", "biliup"].every((name) => diagnostics.tools.some((tool: any) => tool.name === name && tool.available)) &&
    diagnostics.tools.some((tool: any) => ["deno", "node"].includes(tool.name) && tool.available);
  return (
    <div className="settings-stack">
      <TranslationPanel
        config={config}
        busy={busy}
        action={action}
        refresh={refresh}
      />
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>工作目录</h2>
            <p>视频素材可能占用较多空间，建议选择独立的工作文件夹。</p>
          </div>
          <StatusBadge tone={unsaved("work_dir") ? "error" : "success"}>
            {unsaved("work_dir") ? "待保存" : "已保存"}
          </StatusBadge>
        </div>
        <div className="section-body">
          <label className="field">
            素材保存位置
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
                选择目录
              </button>
            </div>
          </label>
          <p className="help">
            更改后只影响新任务；已有素材保持原位。
            {diagnostics && <span> {uiText("当前可用空间")} {bytes(diagnostics.free_bytes)}</span>}
          </p>
          <p className="data-path">任务与设置：<span data-no-localize>{config.data_dir}</span></p>
        </div>
      </section>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>YouTube 下载</h2>
            <p>选择新任务下载源视频时使用的最高分辨率和配音音轨。</p>
          </div>
          <StatusBadge tone={unsaved("youtube_max_height", "youtube_audio_language") ? "error" : "success"}>
            {unsaved("youtube_max_height", "youtube_audio_language") ? "待保存" : "已保存"}
          </StatusBadge>
        </div>
        <div className="section-body">
          <div className="field">
            最高分辨率
            <StyledSelect
              label="YouTube 下载最高分辨率"
              value={String(form.youtube_max_height)}
              onChange={(value) => update("youtube_max_height", Number(value))}
              options={[
                { value: "0", label: "最佳可用画质" },
                ...[2160, 1440, 1080, 720, 480, 360].map((height) => ({
                  value: String(height), label: `${height}p`,
                })),
              ]}
            />
          </div>
          {supportsAudioLanguage ? (
            <div className="field">
              配音音轨
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
            <p className="help">后台尚未加载配音设置。关闭并重新启动桌面程序后即可选择配音。</p>
          )}
          <p className="help">分辨率不足时选更低画质；所选配音不存在时优先回退原声，再使用可用音轨。已有任务继续使用创建时的设置。</p>
        </div>
      </section>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>投稿默认值</h2>
            <p>新建任务时保存参数快照，修改默认值不影响已有任务。</p>
          </div>
          <StatusBadge tone={unsaved("bili_tid", "acfun_channel_id", "bili_tags", "bili_line", "upload_gap_seconds") ? "error" : "success"}>
            {unsaved("bili_tid", "acfun_channel_id", "bili_tags", "bili_line", "upload_gap_seconds") ? "待保存" : "已保存"}
          </StatusBadge>
        </div>
        <div className="section-body form-grid">
          <label className="field">
            投稿分区 ID
            <input
              type="number"
              min={1}
              max={65535}
              value={form.bili_tid}
              onChange={(e) => update("bili_tid", Number(e.target.value))}
            />
          </label>
          <AcfunChannelSelect label="AcFun 默认分区（自动投稿必填）"
            value={form.acfun_channel_id} disabled={busy}
            onChange={(value) => update("acfun_channel_id", value)} />
          <div className="field">
            上传线路
            <StyledSelect
              label="上传线路"
              value={form.bili_line}
              onChange={(value) => update("bili_line", value)}
              options={["tx", "bda2", "qn", "ws", "txa"].map((line) => ({ value: line, label: line }))}
            />
          </div>
          <label className="field">
            标签
            <input
              value={form.bili_tags}
              onChange={(e) => update("bili_tags", e.target.value)}
              placeholder="转载,科技"
            />
          </label>
          <label className="field">
            上传间隔（秒）
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
            <h2>通用设置</h2>
            <p>选择界面语言与主题，并设置素材校验方式。</p>
          </div>
          <StatusBadge tone={unsaved("ui_language", "theme", "hwaccel", "validation_cache") ? "error" : "success"}>
            {unsaved("ui_language", "theme", "hwaccel", "validation_cache") ? "待保存" : "已保存"}
          </StatusBadge>
        </div>
        <div className="section-body">
          <div className="setting-row">
            <div>
              <strong>界面语言</strong>
              <p>保存后切换应用的显示语言。</p>
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
              <strong>界面主题</strong>
              <p>选择浅色、深色或跟随系统。</p>
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
                  {theme.name}
                </button>
              ))}
            </div>
          </div>
          <div className="setting-row">
            <div>
              <strong>完整校验</strong>
              <p>自动尝试硬件解码，不可用时回退 CPU。</p>
            </div>
            <StyledSelect
              label="完整校验"
              value={form.hwaccel}
              onChange={(value) => update("hwaccel", value)}
              options={[{ value: "auto", label: "自动选择" }, { value: "cpu", label: "仅 CPU" }]}
            />
          </div>
          <label className="setting-row">
            <div>
              <strong>复用完整校验缓存</strong>
              <p>仍检查整文件 SHA-256，命中后跳过重复解码。</p>
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
        <span className="help">任务运行期间不能修改全局设置。</span>
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            action(async () => {
              const {
                work_dir,
                bili_tid,
                acfun_channel_id,
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
          保存设置
        </button>
      </div>
      <section className="settings-card">
        <div className="section-title">
          <div>
            <h2>环境与数据</h2>
            <p>检查本地工具，或导入命令行版本的任务记录。</p>
          </div>
          <div className="section-actions">
            <StatusBadge tone={!diagnostics ? "neutral" : toolsReady ? "success" : "error"}>
              {!diagnostics ? "待检测" : toolsReady ? "工具就绪" : "需处理"}
            </StatusBadge>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                action(async () =>
                  setDiagnostics(await request("system.diagnostics")),
                )
              }
            >
              <RefreshCw size={14} />
              重新检测
            </button>
          </div>
        </div>
        <div className="section-body">
          <div className="tool-list">
            {diagnostics?.tools.map((tool: any) => (
              <div className="tool-row" key={tool.name}>
                <span>
                  {tool.available ? (
                    <CheckCircle2 size={16} />
                  ) : (
                    <CircleAlert size={16} />
                  )}
                  {tool.name}
                </span>
                <small title={tool.path}>{tool.version}</small>
              </div>
            ))}
          </div>
          <p className="help">
            YouTube 解析需要 Deno 或 Node.js
            中至少一个。第一期开发版使用本机工具，后续安装包将随附依赖。
          </p>
          <div className="button-row">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setImportConfirm(true)}
            >
              导入旧项目任务
            </button>
            <button className="secondary" disabled={busy} onClick={exportLogs}>
              <ArrowDownToLine size={15} />
              导出诊断日志
            </button>
          </div>
        </div>
      </section>
      {importConfirm && (
        <Modal title="导入旧项目记录" close={() => setImportConfirm(false)}>
          <p className="modal-copy">
            请先停止旧命令行程序。将复制任务记录并保留原素材位置，不会移动视频或导入密钥。已存在的视频
            ID 会跳过。
          </p>
          <div className="modal-actions">
            <button
              className="secondary"
              onClick={() => setImportConfirm(false)}
            >
              取消
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                action(async () => {
                  const path = await chooseDirectory();
                  if (path) {
                    await request("data.import", { path });
                    setImportConfirm(false);
                  }
                }, "旧任务记录已导入。")
              }
            >
              选择旧项目目录
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
