import { test, expect } from "@playwright/test";

test("image uploader validates required fields, previews, sorts and retains its draft", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建投稿任务/ }).click();
  await expect(page.getByRole("tab", { name: "视频上传" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "图文上传" }).click();
  const panel = page.getByRole("tabpanel", { name: "图文上传" });
  const queue = panel.getByRole("button", { name: "加入图文队列" });
  await expect(queue).toBeDisabled();
  await expect(panel.getByText("添加图片开始创作")).toBeVisible();
  for (const path of ["/photos/first.png", "/photos/second.jpg"]) {
    await panel.getByLabel("图片文件路径").fill(path);
    await panel.getByRole("button", { name: "添加此图片" }).click();
  }
  await expect(panel.locator(".post-image-card")).toHaveCount(2);
  await panel.getByRole("button", { name: "后移图片 1" }).click();
  await expect(panel.locator(".post-image-card strong").first()).toHaveText("1. second.jpg");
  await panel.getByLabel("图文正文").fill("我的图文正文\n保留第二行 🌸");
  await panel.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await expect(queue).toBeDisabled();
  await panel.getByRole("checkbox", { name: /我拥有这些图片与文字/ }).check();
  await expect(queue).toBeEnabled();
  await page.getByRole("tab", { name: "视频上传" }).click();
  await expect(page.getByRole("tabpanel", { name: "视频上传" }).getByLabel("YouTube 视频链接")).toBeVisible();
  await page.getByRole("tab", { name: "图文上传" }).click();
  await expect(panel.getByLabel("图文正文")).toHaveValue("我的图文正文\n保留第二行 🌸");
  await expect(panel.locator(".post-image-card")).toHaveCount(2);
  await page.screenshot({ path: "../output/image-post-upload.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.getByRole("button", { name: "移除图片 1" }).click();
  await panel.getByRole("button", { name: "移除图片 1" }).click();
  await expect(queue).toBeDisabled();
  await expect(panel.getByText("添加图片开始创作")).toBeVisible();
});

test("image uploader limits the gallery to nine images", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建投稿任务/ }).click();
  await page.getByRole("tab", { name: "图文上传" }).click();
  const panel = page.getByRole("tabpanel", { name: "图文上传" });
  for (let i = 1; i <= 9; i++) {
    await panel.getByLabel("图片文件路径").fill(`/photos/${i}.png`);
    await panel.getByRole("button", { name: "添加此图片" }).click();
    await expect(panel.locator(".post-image-card")).toHaveCount(i);
  }
  await expect(panel.getByRole("button", { name: "选择图片" })).toBeDisabled();
  await panel.getByLabel("图片文件路径").fill("/photos/10.png");
  await expect(panel.getByRole("button", { name: "添加此图片" })).toBeDisabled();
  await panel.getByRole("button", { name: "移除图片 9" }).click();
  await expect(panel.getByRole("button", { name: "添加此图片" })).toBeEnabled();
});

test("image task detail previews the gallery and saves body edits before confirmation", async ({ page }) => {
  await page.goto("/?preview&accounts=1&imagePosts");
  await page.getByRole("button", { name: /图文动态预览/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await expect(dialog.locator(".post-image-card")).toHaveCount(2);
  await expect(dialog.getByLabel(/中文标题/)).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "重新翻译" })).toHaveCount(0);
  await dialog.getByLabel("图文正文").fill("编辑后的图文正文\n第二行 🌸");
  await expect(dialog.getByRole("button", { name: "确认投稿", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "确认投稿", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("heading", { name: "编辑后的图文正文", exact: true })).toBeVisible();
  await page.screenshot({ path: "../output/image-post-preview.png" });
});

test("uncertain dynamic publication asks for a numeric post ID and account feed review", async ({ page }) => {
  await page.goto("/?preview&accounts=1&imagePosts&imagePostUnknown");
  await page.getByRole("button", { name: /图文动态预览/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await expect(dialog.getByRole("button", { name: "打开账号动态" })).toBeVisible();
  await expect(dialog.getByLabel("登记动态 ID")).toBeVisible();
  await expect(dialog.getByLabel("登记 BV 号")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "继续任务" })).toHaveCount(0);
});

test("image uploader renders in the light theme and English", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: "切换明暗主题" }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "界面语言" }).click();
  await page.getByRole("option", { name: "English" }).click();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await page.locator("nav .nav-item").first().click();
  await page.locator(".page-heading button.primary").click();
  await expect(page.getByRole("dialog", { name: "New submission task", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Image post upload" }).click();
  const panel = page.getByRole("tabpanel", { name: "Image post upload" });
  await panel.getByLabel("Image file path").fill("/photos/first.png");
  await panel.getByRole("button", { name: "Add this image" }).click();
  await expect(panel.getByRole("button", { name: "Remove image 1" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Queue image post" })).toBeDisabled();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.screenshot({ path: "../output/image-post-upload-light.png" });
});
