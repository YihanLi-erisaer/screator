// Explicit development-only preview. Never used by the native desktop transport.
import type { Task } from "./types";
const config = {
  work_dir: "D:\\yt2bili-work",
  bili_tid: 171,
  bili_tags: "转载",
  acfun_channel_id: 90,
  bili_line: "tx",
  youtube_max_height: 0,
  youtube_audio_language: "auto",
  upload_gap_seconds: 20,
  theme: "dark",
  ui_language: "zh-CN",
  hwaccel: "auto",
  validation_cache: true,
  has_deepl_key: false,
  translation_primary: "local_llm",
  translation_fallback_enabled: true,
  translation_ready: false,
  local_llm_mode: "managed",
  local_llm_base_url: "http://127.0.0.1:11435",
  local_llm_model: "qwen3.5:4b",
  local_llm_backend: "auto",
  local_llm_backends: ["auto", "vulkan", "cpu"],
  local_llm_timeout_seconds: 300,
  translation_total_timeout_seconds: 420,
  data_dir: "本地应用数据目录",
  youtube_cookies: false,
  vault_error: "",
};
const number = Math.min(
  5,
  Math.max(
    0,
    Number(new URLSearchParams(location.search).get("accounts") || 0),
  ),
);
const accounts = Array.from({ length: number }, (_, i) => ({
  account_id: `account-${i + 1}`,
  uid: String(10001 + i),
  nickname: `账号 ${i + 1}`,
  remark: "",
  lifecycle: "active",
  slot: i + 1,
  auth_state: "valid",
}));
let localInstalled = false;
let delayMetadataRefresh = false;
const translationJobs: any[] = [];
let tasks: Task[] = [];
if (new URLSearchParams(location.search).has("populated"))
  tasks = [
    {
      video_id: "abcdefghijk",
      title_zh: "用更少的工具，构建更专注的工作流",
      title_orig: "A focused creative workflow",
      status: "ready",
      uploader: "Studio Notes",
    },
    {
      video_id: "lmnopqrstuv",
      title_zh: "关于设计系统，我们学到了什么",
      title_orig: "Lessons from a design system",
      status: "validating",
      uploader: "Design Journal",
    },
    {
      video_id: "wxyz1234567",
      title_zh: "让创作回归简单",
      title_orig: "Making things simpler",
      status: "submitted",
      uploader: "Creative Process",
    },
  ].map((t) => ({
    ...t,
    task_id: t.video_id,
    account_id: accounts[0]?.account_id || null,
    account_uid_snapshot: accounts[0]?.uid || null,
    account_name_snapshot: accounts[0]?.nickname || "",
    revision: 1,
    url: `https://www.youtube.com/watch?v=${t.video_id}`,
    desc_orig: "An exploration of thoughtful tools and creative work.",
    desc_zh: "探索更简单的工具与创作方式。",
    work_dir: "",
    video_path: "",
    cover_path: "",
    bv_id: t.status === "submitted" ? "BV1234567890" : "",
    error: "",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }));
if (new URLSearchParams(location.search).has("samevideo") && tasks.length)
  tasks = accounts.map((a) => ({
    ...tasks[0],
    task_id: a.account_id + "-samevideo",
    account_id: a.account_id,
    account_uid_snapshot: a.uid,
    account_name_snapshot: a.nickname,
  }));
if (new URLSearchParams(location.search).has("manyTasks") && tasks.length) {
  const submitted = tasks.find((task) => task.status === "submitted") || tasks[0];
  tasks = Array.from({ length: 45 }, (_, index) => ({
    ...submitted,
    task_id: `page-${index + 1}`,
    video_id: `page-${index + 1}`,
    title_zh: `分页记录 ${index + 1}`,
    status: "submitted",
    updated_at: new Date(Date.now() - index * 1000).toISOString(),
  }));
}
if (new URLSearchParams(location.search).has("importedHistory"))
  tasks.push({
    task_id: "imported-preview", video_id: "abcdefghijk", url: "https://youtu.be/abcdefghijk",
    imported_history: true, account_id: null, account_uid_snapshot: "23941395",
    account_name_snapshot: "StarDazz", revision: 1, status: "submission_unknown",
    title_orig: "Original title", title_zh: "迁移的投稿记录", desc_orig: "Original description",
    desc_zh: "已迁移的简介", uploader: "Preview", work_dir: "", video_path: "",
    cover_path: "", bv_id: "", error: "", created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(), publications: [{
      publication_id: "", platform: "acfun", account_id: "", account_label: "AcFun 原账号",
      status: "submitted", revision: 0, text: "AcFun 稿件", remote_id: "123456", error: "",
    }],
  });
const verificationPreview = new URLSearchParams(location.search).has("acfunVerification");
let verificationMissing = new URLSearchParams(location.search).has("acfunVerificationMissing");
if (verificationPreview && tasks.length) {
  tasks[0].status = "partial_success";
  tasks[0].publications = [{
    publication_id: "acfun-verification-preview", platform: "acfun", account_id: "acfun-preview",
    status: "failed", revision: 1, text: "", remote_id: "",
    error: "AcFun 需要安全验证（代码 400011）。",
    snapshot: JSON.stringify({ title: "验证测试", channel_id: 86, description: "共用简介", tags: ["转载"] }),
  }];
}
export async function request(method: string, params: any): Promise<any> {
  if (verificationPreview && method === "acfun.verification") return verificationMissing ? {
    status: "refresh_required", reason: "missing", message: "本次验证入口未保存或来自旧版后台。",
  } : {
    status: "ready", challenge_id: "preview-challenge", url: new URLSearchParams(location.search).has("acfunMobileVerification")
      ? "https://app.m.kuaishou.com/account/verify?preview=1" : "https://passport.kuaishou.com/pc/identity/qrcode?preview=1",
  };
  if (verificationPreview && method === "acfun.verification.refresh") {
    verificationMissing = false;
    return { queued: true };
  }
  if (verificationPreview && method === "acfun.verification.complete") {
    if (params.challenge_id !== "preview-challenge" || params.token !== "preview-proof" || params.verification_type !== "captcha")
      throw new Error("预览验证结果不匹配");
    tasks[0].publications![0].status = "ready";
    tasks[0].publications![0].error = "";
    return { ready: true };
  }
  if (method === "system.health") return { protocol_version: 2 };
  if (method === "translation.status")
    return {
      local: {
        state: localInstalled ? "ready" : "missing",
        message: localInstalled
          ? "开发预览 · 本地组件已就绪"
          : "开发预览 · 尚未安装本地组件",
      },
    };
  if (method === "translation.jobs.get")
    return structuredClone(
      params.job_id
        ? translationJobs.find((j) => j.job_id === params.job_id)
        : { items: translationJobs },
    );
  if (method === "translation.install" || method === "translation.test" || method === "translation.uninstall") {
    const job: any = {
      job_id: crypto.randomUUID(),
      kind: method.endsWith("test") ? "test:" + params.provider : method.endsWith("uninstall") ? "uninstall" : "install",
      state: "running",
    };
    translationJobs.push(job);
    setTimeout(() => {
      if (job.state === "running") {
        job.state = "complete";
        job.result = job.kind === "uninstall"
          ? { message: "本地大语言模型已卸载；运行时和已有译文已保留。" }
          : { title: "更好的工作流", description: "构建实用工具。保留版本 2.0。", elapsed_ms: 800 };
        if (job.kind === "install") localInstalled = true;
        if (job.kind === "uninstall") localInstalled = false;
        config.translation_ready = localInstalled || config.has_deepl_key;
      }
    }, 1000);
    return { job_id: job.job_id };
  }
  if (method === "translation.jobs.cancel") {
    translationJobs.find((j) => j.job_id === params.job_id).state = "cancelled";
    return { requested: true };
  }
  if (method === "tasks.retranslate") {
    const task = tasks.find((t) => t.task_id === params.task_id)!;
    task.title_zh = "";
    task.desc_zh = "";
    task.status = "queued_upload";
    task.translation = { state: "queued" };
    return { queued: true };
  }

  if (method === "settings.get") {
    const result: Record<string, unknown> = { ...config };
    if (new URLSearchParams(location.search).has("legacyAudioSettings")) delete result.youtube_audio_language;
    return result;
  }
  if (method === "settings.update") {
    if (new URLSearchParams(location.search).has("legacyAudioSettings") &&
        "youtube_audio_language" in params.values) throw new Error("包含不支持的设置项。");
    Object.assign(config, params.values);
    return { ...config };
  }
  if (method === "credentials.set") {
    config.has_deepl_key = Boolean(String(params.value ?? "").trim());
    config.translation_ready = localInstalled || config.has_deepl_key;
    return { saved: true };
  }
  if (method === "auth.status")
    return {
      accounts,
      configured: accounts.length > 0,
      login: { status: "idle" },
    };
  if (method === "douyin.auth.status") {
    const enabled = new URLSearchParams(location.search).get("douyin") === "1";
    const unconfiguredError = new URLSearchParams(location.search).has("douyinUnconfiguredError");
    return {
      configured: enabled,
      can_sync: enabled,
      capabilities: { auto_publish: enabled },
      account: enabled
        ? {
            account_id: "douyin-preview",
            binding_revision: 1,
            nickname: "抖音预览账号",
            auth_state: "valid",
          }
        : null,
      error: !enabled && unconfiguredError
        ? "请先配置已部署的 HTTPS 抖音授权服务地址（只填写域名和端口）。"
        : "",
    };
  }
  if (method === "acfun.channels") return { items: [
    { channel_id: 90, name: "科技 / 科技制造" },
    { channel_id: 196, name: "影视 / 纪录片·短片" },
    { channel_id: 86, name: "生活 / 生活日常" },
    ...(new URLSearchParams(location.search).has("manyChannels")
      ? Array.from({ length: 12 }, (_, index) => ({ channel_id: 300 + index, name: `测试分区 ${index + 1}` }))
      : []),
  ] };
  if (method === "acfun.auth.status") {
    const connected = new URLSearchParams(location.search).get("acfun") === "1";
    const legacyWorker = new URLSearchParams(location.search).has("acfunLegacyWorker");
    return {
      can_sync: connected && !legacyWorker, capabilities: { auto_publish: true, experimental: true },
      account: connected ? { account_id: "acfun-preview", binding_revision: 1, user_id: "12345", nickname: "AcFun 预览账号", auth_state: "valid" } : null,
      error: legacyWorker ? "AcFun 实验性网页投稿尚未启用。"
        : !connected && new URLSearchParams(location.search).has("acfunNoLoginError")
          ? "请先登录 AcFun。" : "",
    };
  }
  if (method === "accounts.list") return { items: accounts, limit: 5 };
  if (method === "data_center.list") {
    const all = tasks.flatMap((task) => (task.publications?.length ? task.publications : task.status === "submitted" ? [{
      publication_id: task.task_id + "-bili", platform: "bilibili" as const, account_id: task.account_id || "", account_label: "",
      status: "submitted", remote_id: task.bv_id,
    }] : []).filter((pub) => ["submitted", "submission_unknown"].includes(pub.status)).map((pub) => ({
      ...pub, task_id: task.task_id, title_zh: task.title_zh, title_orig: task.title_orig,
      account_name: pub.account_label || task.account_name_snapshot,
      review_status: pub.platform === "bilibili" ? "审核通过" : null,
      views: pub.platform === "bilibili" ? 1234 : null, likes: pub.platform === "bilibili" ? 62 : null,
      comments: pub.platform === "bilibili" ? 8 : null, favorites: pub.platform === "bilibili" ? 17 : null,
    })));
    const filtered = all.filter((item) => (!params.platform || item.platform === params.platform)
      && (!params.account_id || item.account_id === params.account_id));
    if (new URLSearchParams(location.search).has("slowData")) await new Promise((resolve) => setTimeout(resolve, 500));
    return { items: filtered.slice(params.offset || 0, (params.offset || 0) + (params.limit || 20)), total: filtered.length,
      accounts: accounts.map((account) => ({ account_id: account.account_id, uid: account.uid, name: account.nickname,
        followers: 12345, views: 987654, publications: 27 })), updated_at: Date.now() / 1000 };
  }
  if (method === "auth.login.cancel") return { cancelled: true };
  if (method === "system.diagnostics")
    return {
      tools: ["ffmpeg", "ffprobe", "biliup", "node"].map((name) => ({
        name,
        available: true,
        version: "开发预览 · 未执行检测",
        path: "",
      })),
      free_bytes: 128 * 1024 ** 3,
    };
  if (method === "tasks.list") {
    const slowTasks = new URLSearchParams(location.search).get("slowTasks");
    if (!params.history && slowTasks !== null) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(2000, Number(slowTasks) || 500)));
    }
    if (params.history && new URLSearchParams(location.search).has("slowHistory")) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const today = new Date();
    const localDate = (day: Date) =>
      `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const trendDates = Array.from({ length: 30 }, (_, index) => {
      const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29 + index);
      return localDate(day);
    });
    const items = tasks.filter(
      (t) =>
        (!params.account_id || t.account_id === params.account_id) &&
        (!params.history || ["submitted", "submission_unknown", "partial_success", "completed_with_abandon"].includes(t.status)) &&
        (!params.status || params.status === t.status) &&
        (!params.search || t.title_zh.includes(params.search)),
    );
    return {
      items: items.slice(params.offset ?? 0, (params.offset ?? 0) + (params.limit ?? 20)),
      total: items.length,
      all_total: params.history
        ? tasks.filter((t) => ["submitted", "submission_unknown", "partial_success", "completed_with_abandon"].includes(t.status)).length
        : tasks.length,
      counts: {
        ready: tasks.filter((t) => t.status === "ready").length,
        validating: tasks.filter((t) => t.status === "validating").length,
      },
      today_submitted_by_account: Object.fromEntries(
        accounts.map((account) => [
          account.account_id,
          tasks.filter(
            (task) =>
              task.account_id === account.account_id &&
              task.status === "submitted" &&
              new Date(task.updated_at).toDateString() === new Date().toDateString(),
          ).length,
        ]),
      ),
      submission_trend: params.history ? {
        dates: trendDates,
        by_account: Object.fromEntries(accounts.map((account) => [
          account.account_id,
          trendDates.map((date) => tasks.filter((task) =>
            !task.imported_history && task.account_id === account.account_id &&
            task.status === "submitted" &&
            localDate(new Date(task.updated_at)) === date,
          ).length),
        ])),
      } : null,
      queue: {
        active: [],
        download: {},
        validate: {},
        uploads: accounts.map((a) => ({
          account_id: a.account_id,
          queued_count: 0,
        })),
      },
    };
  }
  if (method === "tasks.get") {
    if (delayMetadataRefresh && new URLSearchParams(location.search).has("slowTaskRefresh")) {
      delayMetadataRefresh = false;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return tasks.find((t) => t.task_id === params.task_id);
  }
  if (method === "tasks.cancel") {
    const task = tasks.find((t) => t.task_id === params.task_id)!;
    task.status = "cancelled";
    task.revision += 1;
    return { requested: true, status: task.status };
  }
  if (method === "history.delete") {
    const index = tasks.findIndex((t) => t.task_id === params.task_id);
    if (!params.confirmed || index < 0 || tasks[index].revision !== params.expected_revision)
      throw new Error("投稿记录已变化，请刷新后重试。");
    tasks.splice(index, 1);
    return { deleted: true };
  }
  if (method === "history.delete_all") {
    if (params.confirmed !== true) throw new Error("请先在弹窗中确认删除全部投稿记录。");
    const historyStatuses = new Set(["submitted", "submission_unknown", "partial_success", "completed_with_abandon"]);
    let deleted = 0;
    for (let index = tasks.length - 1; index >= 0; index--) {
      if (historyStatuses.has(tasks[index].status)) {
        tasks.splice(index, 1);
        deleted++;
      }
    }
    return { deleted };
  }
  if (method === "tasks.cover") return { image: null };
  if (method === "logs.tail") return { items: [] };
  if (method === "tasks.update_metadata") {
    const task = tasks.find((t) => t.task_id === params.task_id)!;
    task.revision += 1;
    task.title_zh = params.title;
    task.desc_zh = params.description;
    delayMetadataRefresh = true;
    return task;
  }
  if (method === "tasks.create")
    throw new Error("当前为界面预览，请在桌面应用中创建真实任务。");
  throw new Error("该操作需要真实桌面服务，预览不会调用外部账号。");
}
