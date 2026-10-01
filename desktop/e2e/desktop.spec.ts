import { test, expect } from "@playwright/test";

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
  await page.getByLabel("首选失败时使用另一服务").uncheck();
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
  await expect(page.locator(".translation-panel .notice.success")).toHaveText("本地大语言模型已卸载；运行时和已有译文已保留。");
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
  await expect(page.getByRole("dialog")).toContainText("欢迎使用 yt2bili");
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
  await page.goto("/?preview&populated&accounts=5&samevideo");
  await expect(page.locator(".task-row")).toHaveCount(5);
  await page.getByRole("combobox", { name: "按账号筛选" }).click();
  await page.getByRole("option", { name: /UID 10005/ }).click();
  await expect(page.locator(".task-row")).toHaveCount(1);
  await page.locator(".task-row").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("UID 10005");
  await dialog.getByLabel(/中文标题/).fill("仅第五账号的标题");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await page.getByRole("combobox", { name: "按账号筛选" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await expect(page.locator(".task-row")).toContainText("用更少的工具");
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
  await expect(page.locator(".task-row")).toHaveCount(1);
});

test("publishing history exposes transfer actions only in the desktop app", async ({ page }) => {
  await page.goto("/?preview&populated");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await expect(page.getByRole("button", { name: "导入投稿记录" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "导出投稿记录" })).toBeDisabled();
  await expect(page.getByRole("button", { name: /全部记录/ })).toBeVisible();
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
  await expect(page.getByRole("status")).toContainText("本机投稿记录已删除");
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
