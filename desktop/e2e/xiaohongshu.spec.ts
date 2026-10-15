import { test, expect, type Page } from "@playwright/test";

async function openImageForm(page: Page, connected = true) {
  await page.goto(`/?preview&accounts=1${connected ? "&xiaohongshu=1" : ""}`);
  await page.getByRole("button", { name: /新建投稿任务/ }).click();
  await page.getByRole("tab", { name: "图文上传" }).click();
  return page.getByRole("tabpanel", { name: "图文上传" });
}

test("image sync requires login and keeps the standalone Bilibili form available", async ({ page }) => {
  const panel = await openImageForm(page, false);
  await expect(panel.getByRole("checkbox", { name: /同步上传小红书/ })).toBeDisabled();
  await expect(panel.getByText("请先到账号与连接登录小红书，登录后可选择同步上传。")).toBeVisible();
  await expect(panel.getByLabel("图文正文")).toHaveAttribute("maxlength", "2000");
  await expect(panel.getByLabel("小红书标题")).toHaveCount(0);
});

test("sync toggle validates title and shared body while retaining the draft", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 780 });
  const panel = await openImageForm(page);
  await panel.getByLabel("图片文件路径").fill("/photos/photo.png");
  await panel.getByRole("button", { name: "添加此图片" }).click();
  await panel.getByLabel("图文正文").fill("共用图文正文");
  await panel.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await panel.getByRole("checkbox", { name: /我拥有这些图片与文字/ }).check();
  const sync = panel.getByRole("checkbox", { name: /同步上传小红书/ });
  const queue = panel.getByRole("button", { name: "加入图文队列" });
  await expect(queue).toBeEnabled();
  await sync.check();
  await expect(queue).toBeDisabled();
  await expect(panel.getByLabel("图文正文")).toHaveAttribute("maxlength", "1000");
  await panel.getByLabel("小红书标题").fill("我的小红书标题");
  await expect(queue).toBeEnabled();
  await sync.uncheck();
  await panel.getByLabel("图文正文").fill("文".repeat(1001));
  await sync.check();
  await expect(panel.getByLabel("小红书标题")).toHaveValue("我的小红书标题");
  await expect(panel.getByText("同步小红书时正文不能超过 1000 字，请缩短正文或取消同步。")).toBeVisible();
  await expect(queue).toBeDisabled();
  await panel.getByLabel("图文正文").fill("适合两个平台的正文");
  await expect(queue).toBeEnabled();
  await page.screenshot({ path: "../output/xiaohongshu-image-sync.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("GIF stays available for Bilibili but blocks Xiaohongshu synchronization", async ({ page }) => {
  const panel = await openImageForm(page);
  await panel.getByLabel("图片文件路径").fill("/photos/animated.gif");
  await panel.getByRole("button", { name: "添加此图片" }).click();
  await panel.getByRole("checkbox", { name: /同步上传小红书/ }).check();
  await expect(panel.getByText("同步小红书不支持 GIF，请替换图片或取消同步。")).toBeVisible();
  await panel.getByRole("checkbox", { name: /同步上传小红书/ }).uncheck();
  await expect(panel.getByText("同步小红书不支持 GIF，请替换图片或取消同步。")).toHaveCount(0);
  await expect(panel.locator(".post-image-card")).toHaveCount(1);
});

test("independent title edits must be saved before confirming both publications", async ({ page }) => {
  await page.goto("/?preview&accounts=1&imagePosts&xiaohongshu=1");
  await page.getByRole("button", { name: /图文动态预览/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  const confirm = dialog.getByRole("button", { name: "确认投稿", exact: true });
  await expect(dialog.getByRole("heading", { name: "各平台投稿结果" })).toBeVisible();
  await dialog.getByLabel("小红书标题").fill("独立的小红书标题");
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("button", { name: "保存小红书标题" }).click();
  await expect(confirm).toBeEnabled();
  await expect(dialog.getByRole("button", { name: "打开小红书创作中心" })).toBeVisible();
  await confirm.click();
  await expect(dialog.getByText(/并同步到小红书/)).toBeVisible();
});

test("uncertain Xiaohongshu result offers account review without a retry action", async ({ page }) => {
  await page.goto("/?preview&accounts=1&imagePosts&xhsUnknown");
  await page.getByRole("button", { name: /图文动态预览/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await expect(dialog.getByRole("button", { name: "打开小红书创作中心" })).toBeVisible();
  await expect(dialog.getByLabel("核对后的作品 ID")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "继续此目标并预览" })).toHaveCount(0);
});

test("Xiaohongshu account panel connects through the browser login flow", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "小红书视频与图文同步 · 单账号" }) }).last();
  await expect(panel.getByText("尚未绑定小红书账号")).toBeVisible();
  await panel.getByRole("button", { name: "登录小红书", exact: true }).click();
  await expect(panel.getByRole("button", { name: "恢复小红书队列" })).toBeVisible();
  await expect(panel.getByText("已连接", { exact: true })).toBeVisible();
});
