import { test, expect } from "@playwright/test";

test("data center loads account metrics and pages through recorded submissions", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=2&populated&manyTasks&slowData");
  const nav = page.getByRole("navigation", { name: "主导航" }).getByRole("button");
  await expect(nav).toHaveText(["任务中心", "投稿记录", "数据中心", "账号与连接", "设置"]);
  await page.getByRole("button", { name: "数据中心", exact: true }).click();
  await expect(page.getByRole("status", { name: "正在加载稿件数据" })).toBeVisible();
  await expect(page.locator(".data-account-card")).toHaveCount(2);
  await expect(page.locator(".data-row")).toHaveCount(20);
  await expect(page.getByText("45 条稿件")).toBeVisible();
  await expect(page.locator(".data-row").first()).toContainText("审核通过");
  await expect(page.locator(".data-row").first()).toContainText("1,234");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".data-row")).toHaveCount(20);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".data-row")).toHaveCount(5);
  await page.getByRole("button", { name: "AcFun", exact: true }).click();
  await expect(page.getByText("还没有稿件数据")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("data center account filter stays aligned with tabs and clear of the table", async ({ page }) => {
  await page.goto("/?preview&accounts=2&populated");
  await page.getByRole("button", { name: "数据中心", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "按账号筛选" })).toBeVisible();
  for (const width of [1024, 600]) {
    await page.setViewportSize({ width, height: 680 });
    const positions = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().toJSON();
      return {
        label: box(".data-account-filter > span"),
        select: box(".data-account-filter .styled-select"),
        toolbar: box(".data-toolbar"),
        table: box(".data-table-head"),
      };
    });
    expect(Math.abs((positions.label.top + positions.label.bottom) / 2 - (positions.select.top + positions.select.bottom) / 2)).toBeLessThan(3);
    expect(positions.toolbar.bottom).toBeLessThanOrEqual(positions.table.top + 1);
    await page.getByRole("combobox", { name: "按账号筛选" }).click();
    const menu = await page.getByRole("listbox", { name: "按账号筛选" }).boundingBox();
    expect(menu).not.toBeNull();
    expect(menu!.x).toBeGreaterThanOrEqual(0);
    expect(menu!.x + menu!.width).toBeLessThanOrEqual(width + 1);
    await page.keyboard.press("Escape");
  }
});

test("display language is saved in general settings and updates the interface", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const language = page.getByRole("combobox", { name: "界面语言" });
  await expect(page.getByRole("heading", { name: "通用设置" })).toBeVisible();
  await language.click();
  await page.getByRole("option", { name: "English" }).click();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "General settings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Task center", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Display language" }).click();
  await page.getByRole("option", { name: "繁體中文" }).click();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-HK");
  await expect(page.getByRole("heading", { name: "通用設定" })).toBeVisible();
  await expect(page.getByRole("button", { name: "任務中心", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "介面語言" }).click();
  await page.getByRole("option", { name: "简体中文" }).click();
  await page.getByRole("button", { name: "儲存設定" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByRole("heading", { name: "通用设置" })).toBeVisible();
});

test("task center shows today's successful submissions for each Bilibili account", async ({ page }) => {
  await page.goto("/?preview&accounts=5&populated&slowTasks");
  await expect(page.locator(".queue-overview-skeleton .queue-card")).toHaveCount(10);
  const loadingQueueHeight = await page.locator(".queue-overview").evaluate((element) => element.getBoundingClientRect().height);
  await expect(page.locator(".queue-overview-skeleton")).toHaveCount(0);
  const loadedQueueHeight = await page.locator(".queue-overview").evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(loadingQueueHeight - loadedQueueHeight)).toBeLessThan(2);
  await expect(page.locator(".stats-heading")).toHaveText("B 站账号 · 今日提交成功");
  const cards = page.locator(".stats-strip .stat");
  await expect(cards).toHaveCount(5);
  await expect(cards.first()).toContainText("账号 1");
  await expect(cards.first().locator("strong")).toHaveText("01");
  await expect(cards.nth(1).locator("strong")).toHaveText("00");
  await expect(cards.last()).toContainText("账号 5");
});

test("task center shows skeletons while records are loading", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=1&slowTasks");
  await expect(page.locator(".tasks-rows-skeleton .task-row")).toHaveCount(3);
  await expect(page.locator(".stat-skeleton")).toHaveCount(1);
  await expect(page.locator(".queue-overview-skeleton .queue-card")).toHaveCount(6);
  const loadingStatsHeight = await page.locator(".stats-strip").evaluate((element) => element.getBoundingClientRect().height);
  const loadingQueueHeight = await page.locator(".queue-overview").evaluate((element) => element.getBoundingClientRect().height);
  const loadingPanelHeight = await page.locator(".task-panel").evaluate((element) => element.getBoundingClientRect().height);
  await expect(page.getByText("你的下一条视频，从这里开始")).toHaveCount(0);
  await expect(page.locator(".tasks-rows-skeleton")).toHaveCount(0);
  await expect(page.locator(".task-row")).toHaveCount(3);
  await expect(page.locator(".queue-overview-skeleton")).toHaveCount(0);
  const loadedStatsHeight = await page.locator(".stats-strip").evaluate((element) => element.getBoundingClientRect().height);
  const loadedQueueHeight = await page.locator(".queue-overview").evaluate((element) => element.getBoundingClientRect().height);
  const loadedPanelHeight = await page.locator(".task-panel").evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(loadingStatsHeight - loadedStatsHeight)).toBeLessThan(2);
  expect(Math.abs(loadingQueueHeight - loadedQueueHeight)).toBeLessThan(2);
  expect(Math.abs(loadingPanelHeight - loadedPanelHeight)).toBeLessThan(2);

  await page.getByRole("button", { name: "待预览", exact: true }).click();
  await expect(page.locator(".tasks-rows-skeleton .task-row")).toHaveCount(3);
  await expect(page.locator(".stat-skeleton")).toHaveCount(0);
  await expect(page.locator(".queue-overview-skeleton")).toHaveCount(0);
  await expect(page.locator(".tasks-rows-skeleton")).toHaveCount(0);
  await expect(page.locator(".task-row")).toHaveCount(1);
});

test("task list grows smoothly when more records arrive than the initial skeleton shows", async ({ page }) => {
  await page.goto("/?preview&populated&manyTasks&slowTasks=1200");
  await expect(page.locator(".tasks-rows-skeleton .task-row")).toHaveCount(3);
  const heights = await page.evaluate(() => new Promise<number[]>((resolve) => {
    const slot = document.querySelector(".list-height-slot")!;
    const observer = new MutationObserver(() => {
      if (slot.querySelectorAll(".task-rows:not(.task-rows-skeleton) .task-row").length !== 20) return;
      observer.disconnect();
      const samples: number[] = [];
      const sample = () => {
        samples.push(slot.getBoundingClientRect().height);
        if (samples.length < 14) requestAnimationFrame(sample);
        else resolve(samples);
      };
      requestAnimationFrame(sample);
    });
    observer.observe(slot, { childList: true, subtree: true });
  }));
  expect(heights.at(-1)!).toBeGreaterThan(heights[0] + 1000);
  expect(new Set(heights.map(Math.round)).size).toBeGreaterThan(3);
});

test("publishing history shows a 30-day line for each Bilibili account", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=3&populated");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  const chart = page.getByRole("region", { name: "近30天 Bilibili 投稿数量" });
  await expect(chart).toBeVisible();
  await expect(page.getByRole("region", { name: "队列概览" })).toHaveCount(0);
  await expect(chart.locator("polyline")).toHaveCount(3);
  await expect(chart.locator(".submission-trend-legend > div")).toHaveCount(3);
  await expect(chart.locator(".submission-trend-legend > div").first()).toContainText("1 条");
  await expect(chart.locator(".submission-trend-legend > div").nth(1)).toContainText("0 条");
  const colors = await chart.locator("polyline").evaluateAll((lines) => lines.map((line) => line.getAttribute("stroke")));
  expect(new Set(colors).size).toBe(3);
  const svg = chart.locator(".submission-trend-chart svg");
  await expect.poll(async () => svg.evaluate((element) => Math.abs(element.viewBox.baseVal.width - element.getBoundingClientRect().width))).toBeLessThan(2);
  await svg.hover({ position: { x: 240, y: 120 } });
  await expect(chart.locator(".trend-tooltip > div")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("publishing history shows skeletons until its records finish loading", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=1&slowHistory");
  await expect(page.locator(".task-row")).toHaveCount(3);
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await expect(page.locator(".history-rows-skeleton .task-row")).toHaveCount(5);
  await expect(page.locator(".history-trend-skeleton")).toBeVisible();
  const loadingTrendHeight = await page.locator(".history-trend-skeleton").evaluate((element) => element.getBoundingClientRect().height);
  await expect(page.getByText("还没有投稿记录")).toHaveCount(0);
  await expect(page.locator(".history-rows-skeleton")).toHaveCount(0);
  await expect(page.locator(".task-row")).toHaveCount(1);
  await expect(page.locator(".submission-trend-chart")).toBeVisible();
  const loadedTrendHeight = await page.locator(".submission-trend").evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(loadingTrendHeight - loadedTrendHeight)).toBeLessThan(20);
});

test("local-first translation settings and fallback can be changed", async ({
  page,
}) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByText("本地大模型 · Qwen3.5 4B")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "首选翻译服务" })).toContainText("本地大模型（默认）");
  await page.getByRole("combobox", { name: "首选翻译服务" }).click();
  await page.getByRole("option", { name: "DeepL" }).click();
  await expect(page.getByRole("combobox", { name: "首选翻译服务" })).toContainText("DeepL");
  await page.getByLabel("首选失败时使用另一服务").click();
  await expect(page.getByLabel("首选失败时使用另一服务")).not.toBeChecked();
  await expect(page.getByText(/不自动切换/)).toBeVisible();
  await expect(page.getByLabel("大模型推理超时（秒）")).toHaveValue("300");
  await expect(page.getByLabel("翻译流程总超时（秒）")).toHaveValue("420");
  await page.getByLabel("大模型推理超时（秒）").fill("240");
  await page.getByLabel("翻译流程总超时（秒）").fill("360");
  await page.getByRole("button", { name: "保存超时设置" }).click();
  await expect(page.getByLabel("大模型推理超时（秒）")).toHaveValue("240");
  await expect(page.getByLabel("翻译流程总超时（秒）")).toHaveValue("360");
});

test("GPU inference settings reset Vulkan when switching to an external server", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const device = page.getByRole("combobox", { name: "本地推理设备" });
  await device.click();
  await page.getByRole("option", { name: "Vulkan（Intel / AMD / NVIDIA）", exact: true }).click();
  await expect(device).toContainText("Vulkan");
  await page.getByRole("combobox", { name: "本地运行方式" }).click();
  await page.getByRole("option", { name: "连接已有本机 Ollama", exact: true }).click();
  await expect(device).toContainText("自动选择 GPU / CPU");
  await device.click();
  await expect(page.getByRole("option", { name: /Vulkan/ })).toHaveCount(0);
  await page.getByRole("option", { name: "仅 CPU", exact: true }).click();
  await expect(device).toContainText("仅 CPU");
  await expect(page.getByText(/外部 Ollama 请在服务进程中设置/)).toBeVisible();
});

test("Intel and AMD validation backend can be saved independently of inference", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const validation = page.getByRole("combobox", { name: "完整校验" });
  await validation.click();
  await page.getByRole("option", { name: "D3D11VA（Windows Intel / AMD / NVIDIA）", exact: true }).click();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(validation).toContainText("D3D11VA");
  await expect(page.getByRole("combobox", { name: "本地推理设备" })).toContainText("自动选择 GPU / CPU");
});

test("DeepL key badge follows saved credential state", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const heading = page.locator(".deepl-heading");
  await expect(heading.locator(".pill.missing")).toHaveText("未导入");
  await page.getByLabel("DeepL API 密钥").fill("preview-key");
  await page.getByRole("button", { name: "保存密钥" }).click();
  await expect(heading.locator(".pill.imported")).toHaveText("已导入");
  await page.getByRole("button", { name: "清除密钥" }).click();
  await expect(heading.locator(".pill.missing")).toHaveText("未导入");
});

test("connection and settings badges follow backend and saved values", async ({ page }) => {
  await page.goto("/?preview&accounts=1&douyin=1&acfun=1");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  for (const name of ["抖音同步投稿 · 单账号", "AcFun 同步投稿 · 单账号"]) {
    await expect(page.locator(".status-heading").filter({ hasText: name }).locator(".pill.imported")).toHaveText("已连接");
  }
  await expect(page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: /哔哩哔哩账号/ }) }).locator(".section-title .pill.imported")).toHaveText("已连接");
  await expect(page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "YouTube 访问" }) }).locator(".pill.missing")).toHaveText("未导入");

  await page.getByRole("button", { name: "设置", exact: true }).click();
  const directory = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "工作目录" }) });
  await expect(directory.locator(".section-title .pill.imported")).toHaveText("已保存");
  await directory.locator("input").fill("D:\\new-work");
  await expect(directory.locator(".section-title .pill.missing")).toHaveText("待保存");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(directory.locator(".section-title .pill.imported")).toHaveText("已保存");
});

test("YouTube download resolution is saved from settings", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const card = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "YouTube 下载" }) });
  const select = card.getByRole("combobox", { name: "YouTube 下载最高分辨率" });
  await expect(select).toContainText("最佳可用画质");
  await select.click();
  await page.getByRole("option", { name: "720p" }).click();
  const audio = card.getByRole("combobox", { name: "YouTube 下载配音音轨" });
  await expect(audio).toContainText("自动（YouTube 默认）");
  await audio.click();
  await page.getByRole("option", { name: "中文" }).click();
  await expect(card.locator(".section-title .pill.missing")).toHaveText("待保存");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(select).toContainText("720p");
  await expect(audio).toContainText("中文");
  await expect(card.locator(".section-title .pill.imported")).toHaveText("已保存");
});

test("settings do not send audio language to an older desktop worker", async ({ page }) => {
  await page.goto("/?preview&legacyAudioSettings");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const card = page.locator(".settings-card").filter({ has: page.getByRole("heading", { name: "YouTube 下载" }) });
  await expect(card.getByText("后台尚未加载配音设置。关闭并重新启动桌面程序后即可选择配音。")).toBeVisible();
  await expect(card.getByRole("combobox", { name: "YouTube 下载配音音轨" })).toHaveCount(0);
  await card.getByRole("combobox", { name: "YouTube 下载最高分辨率" }).click();
  await page.getByRole("option", { name: "720p" }).click();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(card.locator(".section-title .pill.imported")).toHaveText("已保存");
});

test("task center and publishing history show twenty records per page", async ({ page }) => {
  await page.goto("/?preview&populated&manyTasks&accounts=1");
  await expect(page.locator(".task-row")).toHaveCount(20);
  await expect(page.locator(".task-row").first()).toContainText("分页记录 1");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".task-row")).toHaveCount(20);
  await expect(page.locator(".task-row").first()).toContainText("分页记录 21");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".task-row")).toHaveCount(5);
  await expect(page.locator(".task-row").first()).toContainText("分页记录 41");
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await expect(page.locator(".task-row")).toHaveCount(20);
  await expect(page.locator(".task-row").first()).toContainText("分页记录 1");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".task-row").first()).toContainText("分页记录 21");
});

test("AcFun login enables task selection without an account page switch", async ({ page }) => {
  await page.goto("/?preview&accounts=1&acfun=1");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "启用 AcFun 实验性接入" })).toHaveCount(0);
  await expect(page.locator(".status-heading").filter({ hasText: "AcFun 同步投稿" })).toContainText("已连接");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.getByRole("button", { name: /新建任务/ }).click();
  await expect(page.getByRole("checkbox", { name: /同步上传 AcFun/ })).toBeEnabled();
});

test("unconnected Douyin and AcFun show status without error toasts", async ({ page }) => {
  await page.goto("/?preview&accounts=1&douyinUnconfiguredError&acfunNoLoginError");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await expect(page.locator(".status-heading").filter({ hasText: "抖音同步投稿" })).toContainText("未配置");
  await expect(page.locator(".status-heading").filter({ hasText: "AcFun 同步投稿" })).toContainText("未登录");
  await expect(page.locator(".error-toast")).toHaveCount(0);

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "AcFun 默认分区（自动投稿必填）" })).toBeDisabled();
  await expect(page.locator(".error-toast")).toHaveCount(0);

  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await expect(dialog.getByRole("checkbox", { name: /同步上传抖音/ })).toBeDisabled();
  await expect(dialog.getByRole("checkbox", { name: /同步上传 AcFun/ })).toBeDisabled();
  await expect(dialog.locator(".error-toast")).toHaveCount(0);
});

test("new task explains a stale AcFun backend without showing its obsolete error", async ({ page }) => {
  await page.goto("/?preview&accounts=1&acfun=1&acfunLegacyWorker");
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await expect(dialog.getByRole("checkbox", { name: /同步上传 AcFun/ })).toBeDisabled();
  await expect(dialog).toContainText("AcFun 账号已登录，但后台仍使用旧版配置。请使用最新构建，完整退出并重新打开应用后重试。");
  await expect(dialog).not.toContainText("实验性网页投稿尚未启用");
});

test("managed translation model can be uninstalled from settings", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const uninstall = page.getByRole("button", { name: "卸载大语言模型" });
  await expect(uninstall).toBeDisabled();
  await page.getByRole("button", { name: "安装 / 重试下载" }).click();
  await expect(page.getByText("开发预览 · 本地组件已就绪")).toBeVisible();
  await expect(uninstall).toBeEnabled();
  page.once("dialog", (dialog) => dialog.dismiss());
  await uninstall.click();
  await expect(page.getByText("开发预览 · 本地组件已就绪")).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await uninstall.click();
  await expect(page.locator(".success-toast")).toHaveText(/本地大语言模型已卸载；运行时和已有译文已保留。/);
  await expect(page.getByText("开发预览 · 尚未安装本地组件")).toBeVisible();
  await expect(uninstall).toBeDisabled();
});

test("first-run accepts local test without a DeepL key", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "开始配置" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "下一步" }).click();
  await dialog.getByRole("button", { name: "下一步" }).click();
  await expect(dialog.getByRole("button", { name: "下一步" })).toBeDisabled();
  await dialog.getByRole("button", { name: "本地试译", exact: true }).click();
  await expect(
    dialog.getByText("更好的工作流", { exact: false }),
  ).toBeVisible();
  await expect(dialog.getByRole("button", { name: "下一步" })).toBeEnabled();
  await expect(dialog.getByLabel("DeepL API 密钥")).toHaveValue("");
});

test("retranslation clears the old text and enters the queue after confirmation", async ({
  page,
}) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "重新翻译", exact: true }).click();
  await expect(dialog).toContainText("旧译文不会恢复");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog.getByLabel(/中文标题/)).toHaveValue("");
  await expect(dialog.getByLabel(/简介/)).toHaveValue("");
  await expect(dialog).toContainText("旧译文已清空，等待重新翻译");
});

test("first-run guide starts with storage selection", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "开始配置" }).click();
  await expect(page.getByRole("dialog")).toContainText("欢迎使用 screator");
  await expect(page.getByLabel("素材工作目录")).toHaveValue(/work/);
  await expect(page.getByRole("dialog")).toContainText("运行环境");
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("empty workspace, preview default, and modal keyboard focus", async ({
  page,
}) => {
  await page.goto("/?preview&accounts=5");
  await expect(page.getByText("你的下一条视频，从这里开始")).toBeVisible();
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("radio").first()).toBeChecked();
  await expect(dialog.getByRole("button", { name: "加入队列" })).toBeDisabled();
  await dialog.getByRole("textbox").fill("https://youtu.be/abcdefghijk");
  await dialog.getByRole("checkbox", { name: /我拥有该视频的版权/ }).check();
  await expect(dialog.getByRole("button", { name: "加入队列" })).toBeDisabled();
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await dialog.getByRole("option", { name: /UID 10005/ }).click();
  await dialog.getByRole("button", { name: "加入队列" }).click();
  await expect(dialog.getByRole("alert")).toContainText("界面预览");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
});

test("errors appear as dismissible floating toasts for five seconds", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.clock.install();
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await dialog.getByRole("textbox").fill("https://youtu.be/abcdefghijk");
  await dialog.getByRole("checkbox", { name: /我拥有该视频的版权/ }).check();
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await dialog.getByRole("option", { name: /UID 10001/ }).click();
  await dialog.getByRole("button", { name: "加入队列" }).click();
  const toast = dialog.locator(".error-toast");
  await expect(toast).toContainText("界面预览");
  await expect(toast).toHaveCSS("animation-name", "toast-slide-in");
  const bounds = await toast.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThan(200);
  await toast.getByRole("button", { name: "关闭错误提示" }).click();
  await expect(toast).toHaveCSS("animation-name", "toast-slide-out");
  await page.clock.fastForward(281);
  await expect(toast).toHaveCount(0);
  await dialog.getByRole("button", { name: "加入队列" }).click();
  await expect(toast).toContainText("界面预览");
  await page.clock.fastForward(5001);
  await expect(toast).toHaveClass(/exiting/);
  await page.clock.fastForward(281);
  await expect(toast).toHaveCount(0);
});

test("success messages appear as dismissible floating toasts for five seconds", async ({ page }) => {
  await page.goto("/?preview");
  await page.clock.install();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "保存设置" }).click();
  const toast = page.locator(".success-toast");
  await expect(toast).toContainText("设置已保存。");
  await expect(toast).toHaveCSS("animation-name", "toast-slide-in");
  await expect(page.locator(".notice.success")).toHaveCount(0);
  await toast.getByRole("button", { name: "关闭成功提示" }).click();
  await expect(toast).toHaveCSS("animation-name", "toast-slide-out");
  await page.clock.fastForward(281);
  await expect(toast).toHaveCount(0);
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(toast).toContainText("设置已保存。");
  await page.clock.fastForward(5001);
  await expect(toast).toHaveClass(/exiting/);
  await page.clock.fastForward(281);
  await expect(toast).toHaveCount(0);
});

test("task preview preserves unsaved edits and gates submission", async ({
  page,
}) => {
  await page.goto("/?preview&populated&accounts=1");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog");
  const title = dialog.getByLabel(/中文标题/);
  await title.fill("手动编辑的标题");
  await page.waitForTimeout(1700);
  await expect(title).toHaveValue("手动编辑的标题");
  await expect(
    dialog.getByRole("button", { name: "确认投稿", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await expect(
    dialog.getByRole("button", { name: "确认投稿", exact: true }),
  ).toBeEnabled();
});

test("closing task detail during a save refresh keeps it closed", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=1&slowTaskRefresh");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/中文标题/).fill("快速关闭后的标题");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await dialog.getByRole("button", { name: "关闭对话框" }).click();
  await expect(dialog).toHaveCount(0);
  await page.waitForTimeout(700);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /快速关闭后的标题/ })).toBeVisible();
});

test("settings theme and 1024px layout", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "浅色", exact: true }).click();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  expect(overflow).toBe(false);
});

test("production-style unconnected page never pretends to perform work", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("未连接到后台")).toBeVisible();
  await expect(page.getByRole("button", { name: /新建任务/ })).toBeDisabled();
});
test("five accounts plus Douyin and AcFun and three shared stages have ten lanes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=5");
  await expect(page.locator(".queue-card")).toHaveCount(10);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await expect(page.getByText("哔哩哔哩账号 · 5/5")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "添加账号", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "重新登录", exact: true }),
  ).toHaveCount(5);
});

test("no account cannot create and historical task requires binding", async ({
  page,
}) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  await expect(page.getByRole("dialog")).toContainText("历史账号待确认");
  await expect(
    page.getByRole("button", { name: "确认投稿", exact: true }),
  ).toBeDisabled();
});

test("five copies of one video keep separate edits and fifth-account filter", async ({
  page,
}) => {
  const taskRows = page.locator("button.task-row");
  await page.goto("/?preview&populated&accounts=5&samevideo");
  await expect(taskRows).toHaveCount(5);
  await page.getByRole("combobox", { name: "按账号筛选" }).click();
  await page.getByRole("option", { name: /UID 10005/ }).click();
  await expect(taskRows).toHaveCount(1);
  await taskRows.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("UID 10005");
  await dialog.getByLabel(/中文标题/).fill("仅第五账号的标题");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await page.getByRole("combobox", { name: "按账号筛选" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await expect(taskRows).toContainText("用更少的工具");
});

test("account dropdown supports keyboard selection and dismissal", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=5&samevideo");
  const filter = page.getByRole("combobox", { name: "按账号筛选" });
  await filter.focus();
  await filter.press("ArrowDown");
  await expect(filter).toHaveAttribute("aria-expanded", "true");
  await filter.press("Escape");
  await expect(filter).toHaveAttribute("aria-expanded", "false");
  await expect(filter).toContainText("全部账号");
  await filter.press("ArrowDown");
  await filter.press("Enter");
  await expect(filter).toContainText("UID 10001");
  await expect(page.locator("button.task-row")).toHaveCount(1);
});

test("select popups remain visible beyond clipped cards and inside dialogs", async ({ page }) => {
  await page.goto("/?preview&accounts=5&acfun=1&manyChannels");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const select = page.getByRole("combobox", { name: "AcFun 默认分区（自动投稿必填）" });
  await expect(select).toBeEnabled();
  await select.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await select.click();
  const menu = page.getByRole("listbox", { name: "AcFun 默认分区（自动投稿必填）" });
  await expect(menu).toBeVisible();
  const visibleBeyondCard = await menu.evaluate((element) => {
    const card = element.closest(".settings-card")!.getBoundingClientRect();
    const list = element.getBoundingClientRect();
    const x = list.left + list.width / 2;
    const y = Math.min(list.bottom - 8, card.bottom + 8);
    return list.bottom > card.bottom + 8 && element.contains(document.elementFromPoint(x, y));
  });
  expect(visibleBeyondCard).toBe(true);
  await select.evaluate((element) => element.closest("main")!.scrollBy(0, -30));
  await expect.poll(() => menu.evaluate((element) => {
    const button = element.parentElement!.querySelector(".styled-select-trigger")!.getBoundingClientRect();
    const list = element.getBoundingClientRect();
    return Math.abs(list.top - button.bottom - 5) < 2 || Math.abs(list.bottom - button.top + 5) < 2;
  })).toBe(true);
  await menu.getByRole("option", { name: "测试分区 12（311）" }).click();
  await expect(select).toContainText("测试分区 12");

  await page.setViewportSize({ width: 1024, height: 680 });
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  const dialogMenu = dialog.getByRole("listbox", { name: "目标 Bilibili 账号" });
  await expect(dialogMenu).toBeVisible();
  const [dialogBox, menuBox] = await Promise.all([dialog.boundingBox(), dialogMenu.boundingBox()]);
  expect(menuBox!.y).toBeGreaterThanOrEqual(dialogBox!.y);
  expect(menuBox!.y + menuBox!.height).toBeLessThanOrEqual(dialogBox!.y + dialogBox!.height);
  await dialogMenu.getByRole("option", { name: /UID 10005/ }).click();
  await expect(dialog.getByRole("combobox", { name: "目标 Bilibili 账号" })).toContainText("UID 10005");
});

test("compact select popup stays aligned and usable", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  const browser = page.getByRole("combobox", { name: "浏览器" });
  await browser.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await browser.click();
  const menu = page.getByRole("listbox", { name: "浏览器" });
  await expect(menu).toBeVisible();
  const aligned = await menu.evaluate((element) => {
    const button = element.parentElement!.querySelector(".styled-select-trigger")!.getBoundingClientRect();
    const list = element.getBoundingClientRect();
    return Math.abs(list.left - button.left) < 2 && list.right <= innerWidth - 8;
  });
  expect(aligned).toBe(true);
  await menu.getByRole("option", { name: "Firefox" }).click();
  await expect(browser).toContainText("Firefox");
});

test("publishing history exposes transfer actions only in the desktop app", async ({ page }) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await expect(page.getByRole("button", { name: "导入投稿记录" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "导出投稿记录" })).toBeDisabled();
  await expect(page.getByRole("button", { name: /全部记录/ })).toBeVisible();
});

test("delete all submission history requires a modal confirmation", async ({ page }) => {
  await page.goto("/?preview&populated&importedHistory");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  const removeAll = page.getByRole("button", { name: "删除所有投稿记录", exact: true });
  await expect(removeAll).toBeEnabled();
  await removeAll.click();
  const dialog = page.getByRole("dialog", { name: "删除所有投稿记录" });
  await expect(dialog).toContainText("确定删除本机全部投稿记录？");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("button", { name: /迁移的投稿记录/ })).toBeVisible();

  await removeAll.click();
  await dialog.getByRole("button", { name: "确认删除所有投稿记录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("还没有投稿记录")).toBeVisible();
  await expect(removeAll).toBeDisabled();
  await page.getByRole("button", { name: /^任务中心/ }).click();
  await expect(page.getByRole("button", { name: /关于设计系统/ })).toBeVisible();
});

test("imported publishing history opens as read-only detail", async ({ page }) => {
  await page.goto("/?preview&importedHistory");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("button", { name: /迁移的投稿记录/ }).click();
  const dialog = page.getByRole("dialog", { name: "导入的投稿记录" });
  await expect(dialog).toContainText("只读历史记录");
  await expect(dialog).toContainText("AcFun 稿件");
  await expect(dialog).toContainText("待核对");
  await expect(dialog.getByRole("button", { name: /确认投稿|继续任务|重试|登记已提交稿件|确认历史账号/ })).toHaveCount(0);
});

test("task detail deletes one local publishing record after confirmation", async ({ page }) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("button", { name: /让创作回归简单/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await dialog.getByRole("button", { name: "删除投稿记录" }).click();
  await expect(dialog).toContainText("不会删除平台上的稿件");
  await dialog.getByRole("button", { name: "返回" }).click();
  await expect(dialog.getByRole("button", { name: "确认删除记录" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "删除投稿记录" }).click();
  await dialog.getByRole("button", { name: "确认删除记录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: /让创作回归简单/ })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("本机记录已删除");
});

test("cancelled task stays in the list and reports immediate completion", async ({ page }) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: /关于设计系统/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await dialog.getByRole("button", { name: "取消任务" }).click();
  await expect(dialog).toContainText("已取消");
  await expect(dialog.getByRole("status")).toContainText("任务已取消，记录已保留。");
  await dialog.getByRole("button", { name: "关闭对话框" }).click();
  await expect(page.getByRole("button", { name: /关于设计系统/ })).toBeVisible();
});

test("unsubmitted ready task can be deleted from the task list", async ({ page }) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await dialog.getByRole("button", { name: "删除投稿记录" }).click();
  await expect(dialog).toContainText("未投稿成功的任务会一并删除本机素材");
  await dialog.getByRole("button", { name: "确认删除记录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: /用更少的工具/ })).toHaveCount(0);
});

test("imported history detail can remove its local copy", async ({ page }) => {
  await page.goto("/?preview&importedHistory");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("button", { name: /迁移的投稿记录/ }).click();
  const dialog = page.getByRole("dialog", { name: "导入的投稿记录" });
  await dialog.getByRole("button", { name: "删除投稿记录" }).click();
  await expect(dialog).toContainText("重新导入原文件可以恢复");
  await dialog.getByRole("button", { name: "确认删除记录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: /迁移的投稿记录/ })).toHaveCount(0);
});
