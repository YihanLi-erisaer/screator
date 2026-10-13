// Explicit development-only preview. Never used by the native desktop transport.
import { platformOptions, type Task } from "./types";
const config = {
  work_dir: "D:\\screator-work",
  bili_tid: 171,
  bili_tags: "转载",
  acfun_channel_id: 90,
  youtube_category_id: "22", youtube_privacy_status: "private", youtube_made_for_kids: false,
  bili_line: "tx",
  youtube_max_height: 0,
  youtube_audio_language: "auto",
  upload_gap_seconds: 20,
  theme: "dark",
  ui_language: ["zh-CN", "zh-HK", "en"].includes(new URLSearchParams(location.search).get("uiLanguage") || "")
    ? new URLSearchParams(location.search).get("uiLanguage")! : "zh-CN",
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
let xhsConnected = new URLSearchParams(location.search).get("xiaohongshu") === "1";
if (new URLSearchParams(location.search).has("imagePosts")) {
  const params = new URLSearchParams(location.search);
  const status = params.has("imagePostUnknown") ? "submission_unknown" : params.has("imagePostSubmitted") ? "submitted" : "ready";
  tasks.push({ task_id: "image-post-preview", video_id: "image-preview", source_kind: "image_post", asset_type: "image",
    url: "", account_id: accounts[0]?.account_id || null, account_uid_snapshot: accounts[0]?.uid || null,
    account_name_snapshot: accounts[0]?.nickname || "", revision: 1, status,
    title_orig: "图文动态预览", title_zh: "图文动态预览", desc_orig: "分享图片和文字", desc_zh: "分享图片和文字",
    image_paths: JSON.stringify(["/photos/first.png", "/photos/second.jpg"]),
    work_dir: "/work/image-post-preview", video_path: "", cover_path: "", uploader: "",
    bv_id: status === "submitted" ? "1234567890123456789" : "", error: "",
    created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
  if (params.get("xiaohongshu") === "1" || params.has("xhsUnknown")) {
    tasks[0].publications = [{ publication_id: "bili-image-preview", platform: "bilibili", account_id: accounts[0]?.account_id || "",
      status: "ready", revision: 1, text: tasks[0].title_zh, remote_id: "", error: "" },
      { publication_id: "xhs-preview", platform: "xiaohongshu", account_id: "xhs-account-preview",
      account_label: "小红书预览账号", status: params.has("xhsUnknown") ? "submission_unknown" : "ready", revision: 1,
      text: "小红书标题", remote_id: "", error: "", snapshot: JSON.stringify({ title: "小红书标题", description: tasks[0].desc_zh }) }];
    if (params.has("xhsUnknown")) tasks[0].status = "submission_unknown";
  }
}
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
if (new URLSearchParams(location.search).has("historyFilters") && tasks.length) {
  const template = tasks[0];
  tasks = platformOptions.flatMap(({ value: platform }) =>
    ["submitted", "submission_unknown", "partial_success", "completed_with_abandon"].map((status, index) => ({
      ...template,
      task_id: `history-${platform}-${index}`,
      video_id: `${platform}-${index}`,
      title_zh: `${platform} 投稿 ${index + 1}`,
      status,
      publications: [{
        publication_id: `publication-${platform}-${index}`, platform,
        account_id: "preview-account", status: status === "completed_with_abandon" ? "abandoned" : "submitted",
        revision: 1, text: "", remote_id: "", error: "",
      }],
    })),
  );
}
if (new URLSearchParams(location.search).has("taskFilters") && tasks.length) {
  const template = tasks[0];
  tasks = platformOptions.flatMap(({ value: platform }) =>
    ["ready", "downloaded", "failed"].map((status, index) => ({
      ...template, task_id: `task-${platform}-${index}`, video_id: `${platform}-${index}`,
      title_zh: `${platform} 任务 ${index + 1}`, status,
      publications: [{ publication_id: `publication-${platform}-${index}`, platform,
        account_id: "preview-account", status, revision: 1, text: "", remote_id: "", error: "" }],
    })),
  );
}
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
if (new URLSearchParams(location.search).has("youtubePublication") && tasks.length) {
  tasks[0].status = "partial_success";
  tasks[0].publications = [{
    publication_id: "youtube-publication-preview", platform: "youtube", account_id: "youtube-preview",
    account_label: "YouTube 预览频道", status: "ready", revision: 1, text: "", remote_id: "", error: "",
    snapshot: JSON.stringify({ title: "YouTube 预览标题", title_source: "generated", category_id: "22",
      privacy_status: "private", made_for_kids: false, description: tasks[0].desc_zh, tags: ["转载"] }),
  }];
}
const socialPublication = new URLSearchParams(location.search).get("socialPublication");
if (socialPublication && tasks.length) {
  const platforms = socialPublication === "all" ? ["twitter", "facebook", "tiktok"] : [socialPublication];
  tasks[0].status = "partial_success";
  tasks[0].publications = platforms.filter((platform) => ["twitter", "facebook", "tiktok"].includes(platform)).map((platform) => ({
    publication_id: `social-${platform}-preview`, platform: platform as "twitter" | "facebook" | "tiktok", account_id: `${platform}-preview`,
    account_label: `${platform} 预览账号`, status: "ready", revision: 1, text: "预览视频标题", remote_id: "", error: "",
    snapshot: JSON.stringify({ title: "预览视频标题", title_source: "generated", description: tasks[0].desc_zh,
      privacy_level: "", allow_comment: false, allow_duet: false, allow_stitch: false, brand_organic: false,
      brand_content: false, is_aigc: false, consent: false, original_content: false, cover_timestamp_ms: 0 }),
  }));
}
let resourceSamples = 0;
const materialLibraries = [{ id: "sample-library", name: "示例素材库", path: "示例素材目录" }];
const materialFiles = [
  { name: "示例视频.mp4", kind: "video", size: 2048000, modified: 1791691200 },
  { name: "参考图片.png", kind: "image", size: 102400, modified: 1791691100 },
  { name: "背景音乐.mp3", kind: "audio", size: 524288, modified: 1791691000 },
];
const filesByLibrary = new Map([["sample-library", materialFiles]]);
export async function request(method: string, params: any): Promise<any> {
  if (method === "materials.libraries") return { items: [...materialLibraries], root: "示例素材目录" };
  if (method === "materials.create_library") {
    const path = params.directory.replace(/[\\/]+$/, "");
    const existing = materialLibraries.find(l => l.path === path);
    if (existing) return existing;
    const result = { id: crypto.randomUUID(), name: path.split(/[\\/]/).pop()!, path };
    materialLibraries.push(result);
    filesByLibrary.set(result.id, []);
    return result;
  }
  if (method === "materials.list") return { items: [...(filesByLibrary.get(params.library_id) ?? [])], path: materialLibraries.find(l => l.id === params.library_id)?.path ?? "" };
  if (method === "materials.import") {
    const files = filesByLibrary.get(params.library_id)!;
    for (const path of params.paths as string[]) {
      const name = path.split(/[\\/]/).pop()!;
      const extension = name.split(".").pop()!.toLowerCase();
      const kind = ["mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "ts"].includes(extension) ? "video"
        : ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "weba"].includes(extension) ? "audio" : "image";
      files.push({ name, kind, size: 102400, modified: Date.now() / 1000 });
    }
    return { added: params.paths.length, skipped: 0 };
  }
  if (method === "materials.rename") { const file = filesByLibrary.get(params.library_id)?.find(f => f.name === params.name); if (file) file.name = params.new_name; return { renamed: true }; }
  if (method === "materials.delete") { const files = filesByLibrary.get(params.library_id) ?? []; const index = files.findIndex(f => f.name === params.name); if (index >= 0 && params.confirmed) files.splice(index, 1); return { deleted: true }; }

  if (method === "system.resources") {
    resourceSamples += 1;
    const flags = new URLSearchParams(location.search);
    if (flags.has("resourceError") && resourceSamples <= 2) throw new Error("Preview monitor unavailable");
    const warming = flags.has("resourceWarmup") && resourceSamples <= 2;
    const processes = [
      { pid: 4100, parent_pid: null, name: "screator.exe", cpu_percent: 0.5, memory_bytes: 64 * 1024 ** 2 },
      { pid: 4101, parent_pid: 4100, name: "msedgewebview2.exe", cpu_percent: 2, memory_bytes: 128 * 1024 ** 2 },
      { pid: 4102, parent_pid: 4100, name: "screator-worker.exe", cpu_percent: 1, memory_bytes: 64 * 1024 ** 2 },
      { pid: 4103, parent_pid: 4102, name: "ffmpeg.exe", cpu_percent: 40, memory_bytes: 256 * 1024 ** 2 },
      { pid: 4104, parent_pid: 4102, name: "ollama.exe", cpu_percent: 25, memory_bytes: 512 * 1024 ** 2 },
    ].map((process) => ({ ...process, cpu_percent: warming ? null : process.cpu_percent }));
    return { scope: "application", root_pid: 4100, sampled_at: Date.now() / 1000,
      interval_seconds: warming ? null : 2, logical_cpu_count: 8, system_memory_bytes: 16 * 1024 ** 3,
      cpu_percent: warming ? null : 68.5, memory_bytes: 1024 ** 3, process_count: 5,
      cpu_pending_processes: warming ? 5 : 0, memory_unavailable_processes: 0,
      inaccessible_processes: 0, processes };
  }
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
    const result: Record<string, unknown> = { ...config, readiness_pending: params.check_services === false };
    if (new URLSearchParams(location.search).has("legacyAudioSettings")) delete result.youtube_audio_language;
    return result;
  }
  if (method === "settings.status") {
    const result = { has_deepl_key: config.has_deepl_key, vault_error: config.vault_error,
      translation_ready: config.translation_ready, readiness_pending: false };
    const delay = Number(new URLSearchParams(location.search).get("slowSettingsStatus") || 0);
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
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
  if (method === "xiaohongshu.auth.status") return { can_sync: xhsConnected, error: "", capabilities: { auto_publish: true, experimental: true }, limit: 1,
    account: xhsConnected ? { account_id: "xhs-account-preview", user_id: "0123456789abcdef01234567", nickname: "小红书预览账号", auth_state: "valid", binding_revision: 1 } : null };
  if (method === "xiaohongshu.auth.start") return { status: "waiting" };
  if (method === "xiaohongshu.auth.poll") { xhsConnected = true; return { status: "done" }; }
  if (method === "xiaohongshu.auth.cancel") return { cancelled: true };
  if (method === "xiaohongshu.auth.clear" || method === "xiaohongshu.accounts.archive") { xhsConnected = false; return { cleared: true }; }
  if (method === "xiaohongshu.accounts.resume_uploads") return { resumed: true };
  if (method === "publications.update_metadata" && params.publication_id === "xhs-preview") {
    const pub = tasks.flatMap(task => task.publications || []).find(pub => pub.publication_id === params.publication_id)!;
    pub.snapshot = JSON.stringify({ ...JSON.parse(pub.snapshot || "{}"), title: params.title });
    pub.text = params.title; pub.revision += 1; return pub;
  }
  if (method === "acfun.channels") return { items: [
    { channel_id: 90, name: "科技 / 科技制造" },
    { channel_id: 196, name: "影视 / 纪录片·短片" },
    { channel_id: 86, name: "生活 / 生活日常" },
    ...(new URLSearchParams(location.search).has("manyChannels")
      ? Array.from({ length: 12 }, (_, index) => ({ channel_id: 300 + index, name: `测试分区 ${index + 1}` }))
      : []),
  ] };
  if (method === "publications.update_metadata" && String(params.publication_id).startsWith("youtube-")) {
    const pub = tasks.flatMap((task) => task.publications || []).find((value) => value.publication_id === params.publication_id)!;
    pub.snapshot = JSON.stringify({ ...JSON.parse(pub.snapshot || "{}"), title: params.title, title_source: "manual",
      category_id: params.category_id, privacy_status: params.privacy_status, made_for_kids: params.made_for_kids });
    pub.revision += 1;
    return pub;
  }
  if (method === "social.auth.status" || method === "social.auth.bind") {
    const platform = String(params.platform);
    const connected = method === "social.auth.bind" || new URLSearchParams(location.search).get("social") === "1" || new URLSearchParams(location.search).get(platform) === "1";
    return { can_sync: connected, capabilities: { auto_publish: platform !== "tiktok", official_api: true }, limit: 1, error: "",
      account: connected ? { platform, account_id: `${platform}-preview`, subject_id: "12345", nickname: `${platform} 预览账号`, binding_revision: 1, auth_state: "valid" } : null };
  }
  if (method === "social.creator_info") return { creator_nickname: "TikTok 预览创作者", privacy_level_options: ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
    comment_disabled: true, duet_disabled: false, stitch_disabled: false, max_video_post_duration_sec: 300 };
  if (method === "social.publications.preview") return { opened: true };
  if (method === "publications.update_metadata" && String(params.publication_id).startsWith("social-")) {
    const pub = tasks.flatMap((task) => task.publications || []).find((value) => value.publication_id === params.publication_id)!;
    pub.snapshot = JSON.stringify({ ...JSON.parse(pub.snapshot || "{}"), ...params.social_metadata, title_source: "manual" });
    pub.text = params.social_metadata.title; pub.revision += 1; return pub;
  }
  if (method === "youtube.auth.status") {
    const connected = new URLSearchParams(location.search).get("youtube") === "1";
    return { configured: true, can_sync: connected, capabilities: { auto_publish: true }, error: "", limit: 1,
      account: connected ? { account_id: "youtube-preview", binding_revision: 1, channel_id: "UC1234567890123456789012", nickname: "YouTube 预览频道", auth_state: "valid" } : null };
  }
  if (method === "youtube.categories") return { items: [{ category_id: "22", name: "人物与博客" }, { category_id: "28", name: "科学与技术" }] };
  if (method === "youtube.auth.start") return { status: "waiting" };
  if (method === "youtube.auth.poll") return { status: "done" };
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
        (!params.platform || (t.publications?.length
          ? t.publications.some((publication: any) => publication.platform === params.platform)
          : !t.imported_history && t.source_kind !== "material" && params.platform === "bilibili")) &&
        (!params.status || params.status === t.status) &&
        (!params.search || `${t.title_zh}${t.title_orig}${t.video_id}`.toLowerCase().includes(params.search.toLowerCase())),
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
  if (method === "image_posts.inspect") {
    const paths = params.paths as string[];
    if (!paths.length || paths.length > 9) throw new Error("请选择 1 至 9 张图片。");
    if (paths.some(path => !/\.(jpe?g|png|gif)$/i.test(path))) throw new Error("图片仅支持 JPG、PNG、GIF 格式。");
    return { items: paths.map((path, index) => ({ path, name: path.split(/[\\/]/).pop(), width: 1200, height: 900, size: 245760,
      image: "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320"><rect width="320" height="320" fill="${["#dbeafe", "#fce7f3", "#dcfce7"][index % 3]}"/><text x="160" y="170" text-anchor="middle" font-size="40" fill="#475569">${index + 1}</text></svg>`) })) };
  }
  if (method === "image_posts.images") {
    const task = tasks.find(task => task.task_id === params.task_id);
    return task?.image_paths ? request("image_posts.inspect", { paths: JSON.parse(task.image_paths) }) : { items: [] };
  }
  if (method === "logs.tail") return { items: [] };
  if (method === "tasks.update_metadata") {
    const task = tasks.find((t) => t.task_id === params.task_id)!;
    task.revision += 1;
    task.title_zh = params.title;
    task.desc_zh = params.description;
    for (const pub of task.publications || []) {
      if (pub.platform === "xiaohongshu" && pub.status === "ready") {
        pub.snapshot = JSON.stringify({ ...JSON.parse(pub.snapshot || "{}"), description: params.description });
        pub.revision += 1;
      }
    }
    delayMetadataRefresh = true;
    return task;
  }
  if (method === "tasks.create" || method === "image_posts.create")
    throw new Error("当前为界面预览，请在桌面应用中创建真实任务。");
  throw new Error("该操作需要真实桌面服务，预览不会调用外部账号。");
}
