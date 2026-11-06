import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  CreditCard,
  ExternalLink,
  LogOut,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { request, operationId, preview } from "./bridge";
import { confirmUi, formatDateTime, getUiLanguage, uiText } from "./i18n";
import { startVisiblePolling } from "./polling";
import "./membership.css";

type Tier = "free" | "standard" | "pro";
type ProviderCode = "stripe" | "wechat" | "alipay";
type MemberStatus = {
  configured: boolean;
  service_url: string;
  configuration_error?: string;
  authenticated: boolean;
  recovery_required?: boolean;
  user: { id: string; email: string } | null;
  subscription: {
    tier: Tier;
    expires_at: string | null;
    features: string[];
  } | null;
};
type Plan = {
  code: string;
  tier: "standard" | "pro";
  months: number;
  amount: number;
  currency: string;
};
type Provider = {
  code: ProviderCode;
  name: string;
  payment_type: "qr" | "redirect";
  available: boolean;
};
type MemberOrder = {
  id: string;
  plan_id: string;
  tier: "standard" | "pro";
  months: number;
  amount: number;
  currency: string;
  provider: ProviderCode;
  state: "pending" | "paid" | "revoked";
  expires_at: string;
  created_at: string;
  paid_at: string | null;
  has_payment_link: boolean;
  payment_type: "qr" | "redirect";
  qr_image?: string | null;
  recovery_warning?: boolean;
};
type PendingCheckout = {
  operation_id: string;
  plan_code: string;
  provider: ProviderCode;
  can_dismiss: boolean;
};
type OrderPage = {
  count: number;
  results: MemberOrder[];
  pending_checkouts: PendingCheckout[];
  pending_error: string;
};

const tierName = (tier: Tier) =>
  tier === "pro" ? "Pro" : tier === "standard" ? "Standard" : uiText("未订阅");
const periodName = (months: number) =>
  uiText(months === 12 ? "年付" : months === 3 ? "季付" : "月付");
const money = (amount: number, currency = "CNY") =>
  new Intl.NumberFormat(getUiLanguage(), {
    style: "currency",
    currency,
  }).format(amount / 100);
const stateName = (state: MemberOrder["state"]) =>
  uiText(
    state === "paid" ? "已支付" : state === "revoked" ? "权益已撤销" : "待支付",
  );
const expired = (order: MemberOrder) =>
  !Number.isFinite(Date.parse(order.expires_at)) ||
  Date.parse(order.expires_at) <= Date.now();
const safeQR = (value?: string | null) =>
  Boolean(value && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value));

export default function MembershipPanel({
  paused = false,
}: {
  paused?: boolean;
}) {
  const [status, setStatus] = useState<MemberStatus | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [history, setHistory] = useState<OrderPage>({
    count: 0,
    results: [],
    pending_checkouts: [],
    pending_error: "",
  });
  const [mode, setMode] = useState<"login" | "register" | "forgot">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [period, setPeriod] = useState(1);
  const [provider, setProvider] = useState<ProviderCode | "">("");
  const [agreed, setAgreed] = useState(false);
  const [activeOrder, setActiveOrder] = useState<MemberOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [offset, setOffset] = useState(0);
  const [pollPaused, setPollPaused] = useState(false);
  const generation = useRef(0);
  const offsetRef = useRef(0);
  const selectedOrderId = useRef<string | null>(null);
  const mounted = useRef(false);
  const busyRef = useRef(false);

  const refresh = useCallback(async (pageOffset = 0) => {
    const current = ++generation.current;
    const next = await request<MemberStatus>("membership.status");
    if (!mounted.current || current !== generation.current) return;
    setStatus(next);
    if (!next.authenticated) {
      selectedOrderId.current = null;
      setActiveOrder(null);
      setOffset(0);
      offsetRef.current = 0;
      setHistory({
        count: 0,
        results: [],
        pending_checkouts: [],
        pending_error: "",
      });
    }
    if (!next.configured) return;
    const [nextPlans, nextProviders] = await Promise.all([
      request<Plan[]>("membership.plans"),
      request<Provider[]>("membership.providers"),
    ]);
    if (!mounted.current || current !== generation.current) return;
    setPlans(nextPlans);
    setProviders(nextProviders);
    setProvider((value) =>
      nextProviders.some((item) => item.code === value && item.available)
        ? value
        : (nextProviders.find((item) => item.available)?.code ?? ""),
    );
    if (next.authenticated) {
      const selectedId = selectedOrderId.current;
      const [orders, selected] = await Promise.all([
        request<OrderPage>("membership.orders", { offset: pageOffset }),
        selectedId
          ? request<MemberOrder>("membership.order", { order_id: selectedId })
          : Promise.resolve(null),
      ]);
      if (mounted.current && current === generation.current) {
        setHistory(orders);
        setOffset(pageOffset);
        offsetRef.current = pageOffset;
        if (selected && selectedOrderId.current === selected.id)
          setActiveOrder(selected);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh().catch(
      (e) =>
        mounted.current && setError(String(e instanceof Error ? e.message : e)),
    );
    return () => {
      mounted.current = false;
      generation.current += 1;
    };
  }, [refresh]);

  const run = async (work: () => Promise<void>) => {
    if (paused || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      if (mounted.current) setError(String(e instanceof Error ? e.message : e));
    } finally {
      if (mounted.current) {
        try {
          await refresh(offsetRef.current);
        } catch (e) {
          setError(String(e instanceof Error ? e.message : e));
        }
        busyRef.current = false;
        setBusy(false);
      }
    }
  };

  useEffect(() => {
    if (
      paused ||
      busy ||
      !activeOrder ||
      activeOrder.state !== "pending" ||
      pollPaused
    )
      return;
    const id = activeOrder.id;
    const deadline = Date.now() + 120000;
    return startVisiblePolling(async (isCurrent) => {
      if (Date.now() > deadline) {
        if (isCurrent()) setPollPaused(true);
        return;
      }
      try {
        const order = await request<MemberOrder>("membership.order", {
          order_id: id,
        });
        if (!isCurrent()) return;
        setActiveOrder(order);
        if (order.state !== "pending") {
          setNotice(
            order.state === "paid"
              ? "支付已确认，会员状态已刷新。"
              : "该订单权益已撤销，请查看账户状态或联系管理员。",
          );
          await refresh(offset);
        } else if (expired(order)) setPollPaused(true);
      } catch (e) {
        if (isCurrent()) setError(String(e instanceof Error ? e.message : e));
      }
    }, 3000);
  }, [
    activeOrder?.id,
    activeOrder?.state,
    busy,
    offset,
    paused,
    pollPaused,
    refresh,
  ]);

  const authenticate = (event: FormEvent) => {
    event.preventDefault();
    const suppliedPassword = password;
    void run(async () => {
      setPassword("");
      if (mode === "login") {
        const result = await request<MemberStatus>("membership.login", {
          email,
          password: suppliedPassword,
        });
        if (mounted.current) setStatus(result);
      } else if (mode === "register") {
        await request("membership.register", {
          email,
          password: suppliedPassword,
        });
        setNotice("注册申请已提交，请查收验证邮件；完成验证后返回应用登录。");
        setMode("login");
      } else {
        await request("membership.forgot_password", { email });
        setNotice(
          "如该账号可以重置密码，邮件将发送至邮箱。请在邮件页面设置新密码后返回登录。",
        );
      }
    });
  };

  const selectOrder = async (order: MemberOrder) => {
    selectedOrderId.current = order.id;
    setActiveOrder(order);
    setPollPaused(false);
  };
  const pendingPlanName = (code: string) => {
    const plan = plans.find((item) => item.code === code);
    return plan
      ? `${tierName(plan.tier)} · ${periodName(plan.months)}`
      : uiText("原会员套餐");
  };
  const hasUncertainCheckout = history.pending_checkouts.some(
    (item) => !item.can_dismiss,
  );
  const checkout = (plan: Plan) =>
    void run(async () => {
      if (hasUncertainCheckout) {
        setNotice("有待确认的支付请求，请先恢复原请求，再发起新购买。");
        return;
      }
      const created = await request<MemberOrder>("membership.checkout", {
        plan_code: plan.code,
        provider,
        operation_id: operationId(),
      });
      await selectOrder(created);
      if (created.recovery_warning)
        setNotice(
          "订单已创建，但本机恢复记录未清除。请从已有订单继续，避免重复付款。",
        );
    });

  return (
    <div className="membership-page">
      {error && (
        <p className="membership-feedback error" role="alert">
          {uiText(error)}
        </p>
      )}
      {notice && (
        <p className="membership-feedback" role="status">
          {uiText(notice)}
        </p>
      )}
      <section className="settings-card membership-account">
        <div className="section-title">
          <div>
            <h2>{uiText("Screator 会员账号")}</h2>
            <p>
              {uiText(
                "邮箱会员账号独立于投稿平台授权，不会替换你的 B 站或其他平台登录。",
              )}
            </p>
          </div>
          <button
            className="secondary"
            disabled={busy || paused}
            onClick={() => void run(async () => {})}
          >
            <RefreshCw size={15} />
            {uiText("刷新会员状态")}
          </button>
        </div>
        {!status ? (
          <p aria-live="polite">{uiText("正在读取会员服务状态…")}</p>
        ) : !status.configured ? (
          <div className="membership-empty">
            <ShieldCheck size={28} />
            <h3>{uiText("会员服务尚未配置")}</h3>
            <p>
              {uiText(
                "请联系管理员配置可信的会员服务地址并重启应用。本地创作和已连接平台不受影响。",
              )}
            </p>
            {status.configuration_error && (
              <p role="alert">{uiText(status.configuration_error)}</p>
            )}
            {preview && (
              <small>
                {uiText("界面预览不会创建真实会员账号或模拟支付成功。")}
              </small>
            )}
          </div>
        ) : status.authenticated ? (
          <div className="membership-identity">
            <div>
              <strong data-no-localize>{status.user?.email}</strong>
              <span className="membership-tier">
                {tierName(status.subscription?.tier ?? "free")}
              </span>
              <p>
                {status.subscription?.expires_at
                  ? uiText("当前会员有效至 {time}", {
                      time: formatDateTime(status.subscription.expires_at),
                    })
                  : uiText("当前没有有效会员订阅。")}
              </p>
            </div>
            <button
              className="secondary"
              disabled={busy || paused}
              onClick={() =>
                void run(async () => {
                  const result = await request<{ remote_revoked: boolean }>(
                    "membership.logout",
                  );
                  setPassword("");
                  selectedOrderId.current = null;
                  setActiveOrder(null);
                  setAgreed(false);
                  if (!result.remote_revoked)
                    setNotice(
                      "已退出本机账号，但暂未能撤销云端会话；网络恢复后可重新登录并退出。",
                    );
                })
              }
            >
              <LogOut size={15} />
              {uiText("退出会员账号")}
            </button>
          </div>
        ) : (
          <>
            {status.recovery_required && (
              <p className="membership-feedback">
                {uiText(
                  "系统凭据库发生异常，自动登录已停止。修复凭据库后可重新登录。",
                )}{" "}
              </p>
            )}
            <div
              className="membership-auth-tabs"
              role="tablist"
              aria-label={uiText("会员账号操作")}
            >
              {(["login", "register", "forgot"] as const).map((value) => (
                <button
                  key={value}
                  role="tab"
                  aria-selected={mode === value}
                  className={mode === value ? "selected" : ""}
                  disabled={busy || paused}
                  onClick={() => {
                    setMode(value);
                    setPassword("");
                    setNotice("");
                  }}
                >
                  {uiText(
                    value === "login"
                      ? "登录会员账号"
                      : value === "register"
                        ? "邮箱注册"
                        : "忘记密码",
                  )}
                </button>
              ))}
            </div>
            <form className="membership-auth-form" onSubmit={authenticate}>
              <label>
                {uiText("会员邮箱")}
                <input
                  aria-label={uiText("会员邮箱")}
                  type="email"
                  autoComplete="email"
                  required
                  maxLength={254}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={busy || paused}
                />
              </label>
              {mode !== "forgot" && (
                <label>
                  {uiText("会员密码")}
                  <input
                    aria-label={uiText("会员密码")}
                    type="password"
                    autoComplete={
                      mode === "register" ? "new-password" : "current-password"
                    }
                    required
                    minLength={mode === "register" ? 12 : 1}
                    maxLength={128}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={busy || paused}
                  />
                </label>
              )}
              <button
                className="primary"
                type="submit"
                disabled={busy || paused}
              >
                {uiText(
                  mode === "login"
                    ? "登录"
                    : mode === "register"
                      ? "注册并发送验证邮件"
                      : "发送重置邮件",
                )}
              </button>
            </form>
            <div className="membership-auth-hint">
              <p>
                {uiText(
                  "注册需验证邮箱，密码为 12 至 128 个字符；密码和会员登录令牌不会写入普通设置文件。",
                )}
              </p>
              <button
                className="text-button"
                disabled={busy || paused || !email}
                onClick={() =>
                  void run(async () => {
                    await request("membership.resend_verification", { email });
                    setNotice("如该账号尚未验证，验证邮件将重新发送至邮箱。");
                  })
                }
              >
                {uiText("重新发送验证邮件")}
              </button>
            </div>
          </>
        )}
        {status?.configured && (
          <small className="membership-service" data-no-localize>
            {status.service_url}
          </small>
        )}
      </section>

      {status?.configured && (
        <section className="settings-card">
          <div className="section-title">
            <div>
              <h2>{uiText("选择会员套餐")}</h2>
              <p>
                {uiText(
                  "一次支付购买会员，不自动扣款。金额以服务端返回的人民币价格为准。",
                )}
              </p>
            </div>
          </div>
          <div
            className="membership-periods"
            role="group"
            aria-label={uiText("会员周期")}
          >
            {[1, 3, 12].map((months) => (
              <button
                className={
                  period === months ? "secondary selected" : "secondary"
                }
                aria-pressed={period === months}
                disabled={busy || paused}
                key={months}
                onClick={() => setPeriod(months)}
              >
                {periodName(months)}
              </button>
            ))}
          </div>
          <div className="membership-plan-grid">
            {(["standard", "pro"] as const).map((tier) => {
              const plan = plans.find(
                (item) => item.tier === tier && item.months === period,
              );
              return (
                <article className={`membership-plan ${tier}`} key={tier}>
                  <CreditCard size={24} />
                  <h3>{tierName(tier)}</h3>
                  <p className="membership-price">
                    {plan ? money(plan.amount, plan.currency) : "—"}
                    <small>{periodName(period)}</small>
                  </p>
                  <p>
                    {uiText(
                      tier === "pro"
                        ? "Pro 档位同时包含 Standard 档位权限。"
                        : "Standard 会员订阅。",
                    )}
                  </p>
                  <button
                    className="primary"
                    disabled={
                      busy ||
                      paused ||
                      !status.authenticated ||
                      !plan ||
                      !provider ||
                      !agreed ||
                      Boolean(history.pending_error) ||
                      hasUncertainCheckout
                    }
                    onClick={() => plan && checkout(plan)}
                  >
                    {plan
                      ? uiText("确认购买 {tier} · {price}", {
                          tier: tierName(tier),
                          price: money(plan.amount, plan.currency),
                        })
                      : uiText("套餐暂不可用")}
                  </button>
                </article>
              );
            })}
          </div>
          <label className="membership-provider-label">
            {uiText("支付渠道")}
            <select
              aria-label={uiText("支付渠道")}
              value={provider}
              disabled={
                busy || paused || !providers.some((item) => item.available)
              }
              onChange={(e) => setProvider(e.target.value as ProviderCode)}
            >
              {!providers.some((item) => item.available) && (
                <option value="">{uiText("暂无可用支付渠道")}</option>
              )}
              {providers.map((item) => (
                <option
                  key={item.code}
                  value={item.code}
                  disabled={!item.available}
                >
                  {uiText(item.name)}
                  {!item.available ? uiText("（暂不可用）") : ""}
                </option>
              ))}
            </select>
          </label>
          {!providers.some((item) => item.available) && (
            <p className="membership-auth-hint">
              {uiText("支付渠道尚未就绪，请联系管理员；不会创建模拟支付订单。")}
            </p>
          )}
          {hasUncertainCheckout && (
            <p className="membership-feedback">
              {uiText("有待确认的支付请求，请先恢复原请求，再发起新购买。")}
            </p>
          )}
          <p className="membership-rules">
            {uiText(
              "周期按 1、3、12 个日历月计算。同档续费顺延；不同档位分别计时，同时有效时 Pro 优先，不折现或暂停原订阅。退款或争议可撤销对应订单权益。",
            )}
          </p>
          <label className="membership-consent">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              disabled={busy || paused}
            />
            {uiText(
              "我已了解会员计时、退款与主动购买规则，同意按所选套餐付款。",
            )}
          </label>
          {!status.authenticated && (
            <p>{uiText("请先登录已验证邮箱的会员账号，再购买套餐。")}</p>
          )}
        </section>
      )}

      {activeOrder && status?.authenticated && (
        <section
          className="settings-card membership-checkout"
          aria-labelledby="membership-checkout-heading"
        >
          <div className="section-title">
            <div>
              <h2 id="membership-checkout-heading">{uiText("完成充值")}</h2>
              <p>
                {tierName(activeOrder.tier)} · {periodName(activeOrder.months)}{" "}
                · {money(activeOrder.amount, activeOrder.currency)}
              </p>
            </div>
            <button
              className="text-button"
              onClick={() => {
                selectedOrderId.current = null;
                setActiveOrder(null);
              }}
            >
              {uiText("返回订单列表")}
            </button>
          </div>
          <p>
            <strong>{stateName(activeOrder.state)}</strong>
            {activeOrder.state === "pending" && expired(activeOrder)
              ? uiText(" · 订单已过期")
              : ""}
          </p>
          <small className="membership-order-id" data-no-localize>
            {activeOrder.id}
          </small>
          {activeOrder.state === "pending" && !expired(activeOrder) && (
            <>
              {activeOrder.payment_type === "qr" &&
              safeQR(activeOrder.qr_image) ? (
                <img
                  className="membership-qr"
                  src={activeOrder.qr_image!}
                  alt={uiText("微信支付二维码")}
                />
              ) : activeOrder.has_payment_link &&
                activeOrder.payment_type === "redirect" ? (
                <button
                  className="primary"
                  disabled={busy || paused}
                  onClick={() =>
                    void run(async () => {
                      await request("membership.open_checkout", {
                        order_id: activeOrder.id,
                      });
                    })
                  }
                >
                  <ExternalLink size={15} />
                  {uiText("打开安全收银台")}
                </button>
              ) : (
                <p>
                  {uiText("收银台尚未准备好，请刷新订单或恢复原支付请求。")}
                </p>
              )}
              <p>
                {uiText(
                  activeOrder.payment_type === "qr"
                    ? "请使用微信扫码。支付结果由服务端核实，扫码或关闭页面不会直接开通会员。"
                    : "请在系统浏览器中完成支付，随后返回应用查看服务端确认的结果。",
                )}
              </p>
            </>
          )}
          <button
            className="secondary"
            disabled={busy || paused}
            onClick={() =>
              void run(async () => {
                await selectOrder(
                  await request<MemberOrder>("membership.order", {
                    order_id: activeOrder.id,
                  }),
                );
              })
            }
          >
            <RefreshCw size={15} />
            {uiText("刷新订单状态")}
          </button>
          {activeOrder.state === "pending" && (
            <small>
              {uiText(
                pollPaused
                  ? "自动查询已暂停，请稍后手动刷新；不要重复付款。"
                  : "每 3 秒查询支付结果，最多持续 2 分钟；离开页面会停止自动查询。",
              )}
            </small>
          )}
        </section>
      )}

      {status?.authenticated && (
        <section className="settings-card membership-orders">
          <div className="section-title">
            <div>
              <h2>{uiText("会员订单")}</h2>
              <p>
                {uiText(
                  "网络中断时保留原支付请求；重试会使用原幂等标识，不会重复创建订单。",
                )}
              </p>
            </div>
          </div>
          {history.pending_error && (
            <p className="membership-feedback error">
              {uiText(history.pending_error)}
            </p>
          )}
          {history.pending_checkouts.map((item) => (
            <div className="membership-pending" key={item.operation_id}>
              <div>
                <strong>{uiText("待恢复的支付请求")}</strong>
                <small>
                  {pendingPlanName(item.plan_code)} ·{" "}
                  {uiText(
                    providers.find((value) => value.code === item.provider)
                      ?.name ?? item.provider,
                  )}
                </small>
              </div>
              <button
                className="secondary"
                disabled={busy || paused || item.can_dismiss}
                onClick={() =>
                  void run(async () => {
                    await selectOrder(
                      await request<MemberOrder>("membership.retry_checkout", {
                        operation_id: item.operation_id,
                      }),
                    );
                  })
                }
              >
                {uiText("恢复原支付请求")}
              </button>
              {item.can_dismiss && (
                <button
                  className="text-button"
                  disabled={busy || paused}
                  onClick={() => {
                    if (
                      confirmUi(
                        "这只移除本机恢复提示，不会取消或退款已存在订单。请先核对订单列表，避免重复付款。",
                      )
                    )
                      void run(async () => {
                        await request("membership.dismiss_checkout", {
                          operation_id: item.operation_id,
                        });
                      });
                  }}
                >
                  {uiText("移除恢复提示")}
                </button>
              )}
            </div>
          ))}
          {!history.results.length ? (
            <p className="membership-auth-hint">{uiText("还没有会员订单。")}</p>
          ) : (
            <div className="membership-order-list">
              {history.results.map((item) => (
                <button
                  className="membership-order-row"
                  key={item.id}
                  disabled={busy || paused}
                  onClick={() =>
                    void run(async () => {
                      await selectOrder(
                        await request<MemberOrder>("membership.order", {
                          order_id: item.id,
                        }),
                      );
                    })
                  }
                >
                  <span>
                    <strong>
                      {tierName(item.tier)} · {periodName(item.months)}
                    </strong>
                    <small>{formatDateTime(item.created_at)}</small>
                  </span>
                  <span>{money(item.amount, item.currency)}</span>
                  <span>
                    {item.state === "pending" && expired(item)
                      ? uiText("订单已过期")
                      : stateName(item.state)}
                  </span>
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          )}
          <div className="membership-pagination">
            <small>
              {uiText("共 {count} 笔会员订单", { count: history.count })}
            </small>
            <button
              className="icon-button"
              aria-label={uiText("上一页会员订单")}
              disabled={busy || paused || offset === 0}
              onClick={() =>
                void run(async () => {
                  await refresh(Math.max(0, offset - 20));
                })
              }
            >
              <ChevronLeft size={16} />
            </button>
            <button
              className="icon-button"
              aria-label={uiText("下一页会员订单")}
              disabled={busy || paused || offset + 20 >= history.count}
              onClick={() =>
                void run(async () => {
                  await refresh(offset + 20);
                })
              }
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
