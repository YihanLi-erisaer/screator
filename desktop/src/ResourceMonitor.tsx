import { useEffect, useState } from "react";
import { Activity, Pause, Play } from "lucide-react";
import { preview, request } from "./bridge";
import { bytes, type ResourceSnapshot } from "./types";
import { StatusBadge } from "./StatusBadge";
import { uiText } from "./i18n";

const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;
const memory = (value: number | null) => value === null ? "—" : bytes(value);

export function ResourceMonitor({ paused = false }: { paused?: boolean }) {
  const [snapshot, setSnapshot] = useState<ResourceSnapshot | null>(null);
  const [error, setError] = useState("");
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    if (!enabled || paused) return;
    let active = true;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (!active || inFlight || document.hidden) return;
      inFlight = true;
      try {
        const result = await request<ResourceSnapshot>("system.resources");
        if (active) {
          setSnapshot(result);
          setError("");
        }
      } catch {
        if (active) {
          setSnapshot(null);
          setError("资源监控暂不可用，请检查桌面依赖或更新安装包。将自动重试。");
        }
      } finally {
        inFlight = false;
        if (active && !document.hidden) timer = setTimeout(poll, 2000);
      }
    };
    const visibility = () => {
      clearTimeout(timer);
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", visibility);
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [enabled, paused]);

  return (
    <section className="settings-card resource-monitor" aria-label="应用资源监控">
      <div className="section-title">
        <div className="service-icon"><Activity size={23} /></div>
        <div>
          <h2>应用资源监控</h2>
          <p>汇总 Screator 主程序、界面、后台和工具进程的资源占用。</p>
        </div>
        <div className="section-actions">
          <StatusBadge tone={error ? "error" : "neutral"}>
            {!enabled || paused ? "已暂停" : error ? "暂不可用" : preview ? "示例数据" : "实时监控"}
          </StatusBadge>
          <button className="text-button" disabled={paused} onClick={() => setEnabled((value) => !value)}>
            {enabled ? <Pause size={14} /> : <Play size={14} />}
            {enabled ? "暂停刷新" : "继续刷新"}
          </button>
        </div>
      </div>
      <div className="section-body">
        {error && <p role="alert" className="help resource-error">{error}</p>}
        {!error && !snapshot && <p role="status">正在读取应用资源…</p>}
        {snapshot && <>
          <div className="resource-metrics" aria-label="应用总占用">
            <div><span>总 CPU</span><strong>{percent(snapshot.cpu_percent)}</strong><small>整机占比</small></div>
            <div><span>总内存</span><strong>{memory(snapshot.memory_bytes)}</strong><small>驻留内存合计</small></div>
            <div><span>进程数量</span><strong>{snapshot.process_count}</strong><small>当前监控进程</small></div>
          </div>
          <p className="help resource-update">
            <span>每 2 秒刷新；更新时间</span>{" "}
            <time dateTime={new Date(snapshot.sampled_at * 1000).toISOString()} data-no-localize>
              {new Date(snapshot.sampled_at * 1000).toLocaleTimeString()}
            </time>
          </p>
          {snapshot.scope === "worker" && <p className="help">当前仅统计后台进程树；请重新启动桌面应用以包含主程序和界面。</p>}
          {snapshot.cpu_pending_processes > 0 && <p className="help">部分进程的 CPU 正在采样或暂不可读，当前合计仅包含已取得的数据。</p>}
          {(snapshot.memory_unavailable_processes > 0 || snapshot.inaccessible_processes > 0) &&
            <p className="help resource-error">部分进程无读取权限，当前内存合计可能不完整。</p>}
          <details className="resource-details">
            <summary>查看进程明细</summary>
            <div className="resource-table-scroll">
              <table className="resource-table">
                <thead><tr><th>进程</th><th>PID</th><th>CPU</th><th>内存</th></tr></thead>
                <tbody>{snapshot.processes.map((process) => <tr key={process.pid}>
                  <td data-no-localize title={process.name}>{process.name}</td>
                  <td data-no-localize>{process.pid}</td>
                  <td>{percent(process.cpu_percent)}</td>
                  <td>{memory(process.memory_bytes)}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </details>
          <p className="help resource-scope">
            {uiText("仅统计本应用启动的进程，外部翻译服务不计入。CPU 按整机 0–100% 计算；内存为各进程驻留内存之和，共享内存可能重复计数。GPU、显存、磁盘和网络占用暂不统计。")}
          </p>
        </>}
      </div>
    </section>
  );
}
