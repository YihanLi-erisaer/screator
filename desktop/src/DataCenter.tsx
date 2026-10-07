import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, UserRound, Video } from "lucide-react";
import { request } from "./bridge";
import { StyledSelect } from "./StyledSelect";
import { labels, type BiliAccount } from "./types";

type Platform = "bilibili" | "acfun" | "douyin";
type Summary = { account_id: string; uid: string; name: string; followers: number | null; views: number | null; publications: number | null };
type Submission = {
  publication_id: string; task_id: string; platform: Platform; account_id: string;
  account_name: string; remote_id: string; status: string; title_zh: string; title_orig: string;
  review_status?: string | null; views?: number | null; likes?: number | null;
  comments?: number | null; favorites?: number | null;
};
type Result = { items: Submission[]; total: number; accounts: Summary[]; updated_at: number };
const PAGE_SIZE = 20;
const format = (value: number | null | undefined) => value == null ? "--" : value.toLocaleString();
const platformName: Record<Platform, string> = { bilibili: "Bilibili", acfun: "AcFun", douyin: "抖音" };

export function DataCenter({ accounts, onOpenTask }: { accounts: BiliAccount[]; onOpenTask: (taskId: string) => void }) {
  const [platform, setPlatform] = useState("");
  const [accountId, setAccountId] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => { timer = setTimeout(() => setRevision((value) => value + 1), 300_000); };
    setLoading(true);
    setError("");
    request<Result>("data_center.list", { offset, limit: PAGE_SIZE, platform, account_id: accountId })
      .then((value) => { if (alive) { setResult(value); setLoading(false); schedule(); } })
      .catch(() => { if (alive) { setError("数据暂时无法获取，请稍后重试。"); setLoading(false); schedule(); } });
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [platform, accountId, offset, revision]);

  const summary = result?.accounts ?? [];
  const rows = result?.items ?? [];
  const total = result?.total ?? 0;
  return <>
    <section aria-label="Bilibili 账号数据">
      <div className="stats-heading">Bilibili 账号概览</div>
      <div className="data-account-grid">
        {loading && !result ? Array.from({ length: Math.max(accounts.length, 1) }, (_, index) =>
          <div className="data-account-card" key={index} role="status" aria-label="正在加载账号数据">
            <span className="skeleton-block skeleton-stat-label" />
            <span className="skeleton-block skeleton-stat-value" />
          </div>) : summary.length ? summary.map((account) =>
          <div className="data-account-card" key={account.account_id}>
            <div className="data-account-title"><UserRound size={18} /><strong data-no-localize>{account.name}</strong><small>UID {account.uid}</small></div>
            <div className="data-account-metrics">
              <div><span>粉丝量</span><strong>{format(account.followers)}</strong></div>
              <div><span>总播放量</span><strong>{format(account.views)}</strong></div>
              <div><span>投稿量</span><strong>{format(account.publications)}</strong></div>
            </div>
          </div>) : <div className="data-account-card data-empty-account">连接 Bilibili 账号后显示账号数据。</div>}
      </div>
    </section>
    <section className="task-panel data-panel" aria-label="稿件数据">
      <div className="panel-toolbar data-toolbar">
        <div className="tabs" aria-label="平台筛选">
          {[["", "全部平台"], ["bilibili", "Bilibili"], ["acfun", "AcFun"], ["douyin", "抖音"]].map(([value, label]) =>
            <button key={value} className={platform === value ? "current" : ""} onClick={() => { setPlatform(value); setAccountId(""); setOffset(0); }}>{label}</button>)}
        </div>
        {(platform === "" || platform === "bilibili") && <div className="data-account-filter"><span>按账号筛选</span>
          <StyledSelect label="按账号筛选" value={accountId} onChange={(value) => { setAccountId(value); setOffset(0); }}
            options={[{ value: "", label: "全部账号" }, ...accounts.filter((a) => a.lifecycle === "active").map((a) =>
              ({ value: a.account_id, label: a.remark || a.nickname || `UID ${a.uid}` }))]} />
        </div>}
      </div>
      <div className="data-table-head"><span>稿件</span><span>审核状态</span><span>播放</span><span>点赞</span><span>评论</span><span>收藏</span></div>
      {loading ? <div className="data-rows" role="status" aria-label="正在加载稿件数据">
        {Array.from({ length: rows.length || 5 }, (_, index) => <div className="data-row" key={index} aria-hidden="true">
          <span className="skeleton-block skeleton-title-line" />
          {Array.from({ length: 5 }, (_, metric) => <span className="skeleton-block skeleton-status" key={metric} />)}
        </div>)}
      </div> : error ? <div className="empty-state"><p>{error}</p><button className="secondary" onClick={() => setRevision((value) => value + 1)}>重试</button></div>
        : rows.length ? <div className="data-rows">{rows.map((row) =>
          <button className="data-row" key={row.publication_id} onClick={() => onOpenTask(row.task_id)}>
            <span className="data-title"><span className="video-tile"><Video size={19} /></span><span>
              <strong data-no-localize>{row.title_zh || row.title_orig || row.remote_id || "未命名稿件"}</strong>
              <small>{platformName[row.platform]} · <span data-no-localize>{row.account_name || "历史账号"}</span> · {row.remote_id || "稿件号待核对"} · {labels[row.status] || row.status}</small>
            </span></span>
            <span>{row.review_status || "--"}</span><span>{format(row.views)}</span><span>{format(row.likes)}</span>
            <span>{format(row.comments)}</span><span>{format(row.favorites)}</span>
          </button>)}</div> : <div className="empty-state"><h2>还没有稿件数据</h2><p>完成投稿后，这里会按需读取已记录稿件的数据。</p></div>}
      <div className="panel-footer"><span>数据每五分钟更新一次；AcFun 和抖音审核与互动数据暂不可读取，以 -- 显示。</span>
        <div><span>{loading ? "…" : `${total} 条稿件`}</span>
          <button className="icon-button" aria-label="上一页" disabled={loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}><ChevronLeft size={15} /></button>
          <button className="icon-button" aria-label="下一页" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}><ChevronRight size={15} /></button>
        </div>
      </div>
    </section>
  </>;
}
