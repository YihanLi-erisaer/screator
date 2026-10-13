import { test, expect } from "@playwright/test";

test("YouTube opt-in follows the AcFun preview and automatic workflow", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建投稿任务/ }).first().click();
  await expect(page.getByRole("checkbox", { name: /同步上传 YouTube/ })).toBeDisabled();
  await page.goto("/?preview&accounts=1&youtube=1");
  await expect(page.getByText("YouTube 独立上传", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /新建投稿任务/ }).first().click();
  const dialog = page.getByRole("dialog");
  const sync = dialog.getByRole("checkbox", { name: /同步上传 YouTube/ });
  await expect(sync).toBeEnabled();
  await expect(sync).not.toBeChecked();
  await sync.check();
  await dialog.getByRole("radio", { name: /自动投稿/ }).check();
  await expect(sync).toBeChecked();
  await page.screenshot({ path: "/private/tmp/screator-youtube-new-task.png", fullPage: true });
});

test("YouTube defaults persist category, visibility and audience", async ({ page }) => {
  await page.goto("/?preview&accounts=1&youtube=1");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const category = page.getByRole("combobox", { name: "YouTube 分类", exact: true });
  await expect(category).toBeEnabled();
  await category.click();
  await page.getByRole("option", { name: "科学与技术", exact: true }).click();
  const privacy = page.getByRole("combobox", { name: "YouTube 可见性", exact: true });
  await privacy.click();
  await page.getByRole("option", { name: "不公开列出", exact: true }).click();
  await page.getByRole("checkbox", { name: "YouTube 默认面向儿童", exact: true }).check();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("设置已保存。", { exact: true })).toBeVisible();
  await expect(privacy).toContainText("不公开列出");
  await expect(category).toContainText("科学与技术");
});

test("YouTube account panel reports the connected channel", async ({ page }) => {
  await page.goto("/?preview&accounts=1&youtube=1");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  await expect(page.getByRole("heading", { name: "YouTube 同步投稿 · 单频道", exact: true })).toBeVisible();
  await expect(page.getByText(/YouTube 预览频道 · UC/)).toBeVisible();
  await expect(page.getByRole("button", { name: "检查 YouTube 授权", exact: true })).toBeEnabled();
  await page.getByRole("heading", { name: "YouTube 同步投稿 · 单频道", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/private/tmp/screator-youtube-accounts.png", fullPage: true });
});

test("YouTube preview saves its independent metadata before submission", async ({ page }) => {
  await page.goto("/?preview&accounts=1&youtube=1&populated&youtubePublication");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情", exact: true });
  await dialog.getByRole("textbox", { name: "YouTube 标题", exact: true }).fill("YouTube 独立标题");
  const confirm = dialog.getByRole("button", { name: "确认投稿未完成目标", exact: true });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("button", { name: "保存 YouTube 投稿信息", exact: true }).click();
  await expect(confirm).toBeEnabled();
  await expect(dialog.getByRole("textbox", { name: "YouTube 标题", exact: true })).toHaveValue("YouTube 独立标题");
  await page.screenshot({ path: "/private/tmp/screator-youtube-preview.png", fullPage: true });
});
