import { useEffect, useRef, useState, type MouseEvent } from "react";
import { Activity } from "lucide-react";
import { accountLabel, type BiliAccount } from "./types";

export interface SubmissionTrendData {
  dates: string[];
  by_account: Record<string, number[]>;
}

const colors = [
  "#4f8cff", "#f58b42", "#38b99a", "#b57bf1", "#ed658d",
  "#d2a232", "#32b7d6", "#a6b94b", "#ec7065", "#7785ed",
];
const height = 250;
const top = 20;
const bottom = 208;
const left = 42;
const right = 18;

function axisStep(peak: number) {
  const rough = Math.max(1, peak / 3);
  const power = 10 ** Math.floor(Math.log10(rough));
  const scaled = rough / power;
  return Math.max(1, (scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * power);
}

export function SubmissionTrend({ trend, accounts }: {
  trend: SubmissionTrendData | null;
  accounts: BiliAccount[];
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [hoveredDay, setHoveredDay] = useState<number | null>(null);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const update = () => setWidth(Math.max(1, chart.clientWidth));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(chart);
    return () => observer.disconnect();
  }, [trend?.dates.length]);

  const dates = trend?.dates || [];
  const visibleAccounts = accounts.filter((account) =>
    account.lifecycle === "active" || trend?.by_account[account.account_id]?.some(Boolean),
  );
  const series = visibleAccounts.map((account, index) => ({
    account,
    color: colors[index] || `hsl(${(index * 137.508) % 360} 72% 53%)`,
    values: dates.map((_, day) => trend?.by_account[account.account_id]?.[day] || 0),
  }));
  const peak = Math.max(0, ...series.flatMap((item) => item.values));
  const step = axisStep(peak);
  const ceiling = Math.max(step * 3, Math.ceil(peak / step) * step);
  const ticks = Array.from({ length: Math.round(ceiling / step) + 1 }, (_, index) => index * step);
  const plotWidth = Math.max(1, width - left - right);
  const x = (index: number) => left + (index / Math.max(1, dates.length - 1)) * plotWidth;
  const y = (value: number) => bottom - (value / ceiling) * (bottom - top);
  const total = series.reduce((sum, item) => sum + item.values.reduce((a, b) => a + b, 0), 0);
  const hasSubmissions = total > 0;
  const dateLabel = (value: string) => value.slice(5).replace("-", "/");
  const displayDay = hoveredDay !== null && hoveredDay < dates.length ? hoveredDay : null;
  const onMove = (event: MouseEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = (event.clientX - bounds.left - left) / Math.max(1, bounds.width - left - right);
    setHoveredDay(Math.min(dates.length - 1, Math.max(0, Math.round(position * (dates.length - 1)))));
  };

  return (
    <section className="submission-trend" aria-label="近30天 Bilibili 投稿数量">
      <div className="submission-trend-heading">
        <div className="submission-trend-identity">
          <span className="service-icon submission-trend-icon"><Activity size={20} strokeWidth={1.8} /></span>
          <div>
            <span className="eyebrow">30 DAY ACTIVITY</span>
            <h2>投稿趋势</h2>
            <p>Bilibili · 按本机日期统计已提交稿件</p>
          </div>
        </div>
        <div className="submission-trend-summary">
          <span>近 30 天合计</span>
          <strong>{total}<small> 条</small></strong>
          {dates.length === 30 && <time>{dateLabel(dates[0])} — {dateLabel(dates[29])}</time>}
        </div>
      </div>
      {dates.length === 30 ? (
        <>
          <div className="submission-trend-body">
            <div className="submission-trend-chart" ref={chartRef}>
              <svg
                viewBox={`0 0 ${width} ${height}`}
                role="img"
                aria-label="各账号近30天每日已提交数量折线图"
                tabIndex={hasSubmissions ? 0 : -1}
                onMouseMove={hasSubmissions ? onMove : undefined}
                onMouseLeave={() => setHoveredDay(null)}
                onFocus={() => setHoveredDay(29)}
                onBlur={() => setHoveredDay(null)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                    event.preventDefault();
                    setHoveredDay((day) => Math.min(29, Math.max(0, (day ?? 29) + (event.key === "ArrowLeft" ? -1 : 1))));
                  }
                }}
              >
                {ticks.map((tick) => (
                  <g key={tick}>
                    <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="trend-grid" />
                    <text x={left - 11} y={y(tick) + 4} textAnchor="end" className="trend-axis">{tick}</text>
                  </g>
                ))}
                {[0, 7, 14, 21, 29].map((index) => (
                  <text key={index} x={x(index)} y={height - 9} textAnchor={index === 0 ? "start" : index === 29 ? "end" : "middle"} className="trend-axis">
                    {dateLabel(dates[index])}
                  </text>
                ))}
                {hasSubmissions && series.map(({ account, color, values }) => (
                  <g key={account.account_id}>
                    <polyline
                      points={values.map((value, index) => `${x(index)},${y(value)}`).join(" ")}
                      fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"
                      className="trend-line"
                    />
                    {values[29] > 0 && <circle cx={x(29)} cy={y(values[29])} r="3.5" fill={color} className="trend-end-point" />}
                  </g>
                ))}
                {displayDay !== null && <>
                  <line x1={x(displayDay)} x2={x(displayDay)} y1={top} y2={bottom} className="trend-crosshair" />
                  {series.map(({ account, color, values }) => (
                    <circle key={account.account_id} cx={x(displayDay)} cy={y(values[displayDay])} r="5" fill={color} className="trend-focus-point" />
                  ))}
                </>}
              </svg>
              {displayDay !== null && (
                <div className="trend-tooltip" style={{ left: `${Math.min(width - 138, Math.max(138, x(displayDay)))}px` }}>
                  <strong>{dates[displayDay]}</strong>
                  {series.map(({ account, color, values }) => (
                    <div key={account.account_id}>
                      <i style={{ backgroundColor: color }} />
                      <span>{account.remark || account.nickname || `UID ${account.uid}`}</span>
                      <b>{values[displayDay]}</b>
                    </div>
                  ))}
                </div>
              )}
              {!hasSubmissions && <div className="submission-trend-empty"><Activity size={20} /><span>近 30 天暂无已提交的 Bilibili 稿件</span></div>}
            </div>
          </div>
          <div className="submission-trend-legend" aria-label="账号颜色与近30天投稿总数">
            {series.map(({ account, color, values }) => (
              <div key={account.account_id} title={accountLabel(account)}>
                <i style={{ backgroundColor: color }} />
                <span>{accountLabel(account)}</span>
                <strong>{values.reduce((sum, value) => sum + value, 0)} <small>条</small></strong>
              </div>
            ))}
          </div>
        </>
      ) : <p className="submission-trend-unavailable">暂无趋势统计数据</p>}
    </section>
  );
}
