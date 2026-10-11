import { test, expect } from "@playwright/test";

test("material libraries create, filter, rename and confirm deletion", async ({
  page,
}) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("combobox", { name: "素材库", exact: true }).click();
  await page.getByRole("option", { name: "示例素材库" }).click();
  await expect(page.locator(".material-card")).toHaveCount(3);
  await page.getByLabel("搜索素材").fill("参考");
  await expect(page.locator(".material-card")).toHaveCount(1);
  await page.getByRole("button", { name: "重命名素材" }).click();
  await page.getByLabel("文件名", { exact: true }).fill("新的参考图片.png");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".material-card strong")).toHaveText(
    "新的参考图片.png",
  );
  await page.getByRole("button", { name: "删除素材", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("任务记录会保留");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(1);
  await page.getByRole("button", { name: "删除素材", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(0);
  await page.getByLabel("搜索素材").fill("");
  await expect(page.locator(".material-card")).toHaveCount(2);
  await page.screenshot({
    path: "../output/materials-page.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建素材库", exact: true }).click();
  await page.getByLabel("素材库名称", { exact: true }).fill("旅行素材");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "素材库", exact: true }),
  ).toContainText("旅行素材");
  await expect(page.getByRole("heading", { name: "暂无素材" })).toBeVisible();
});

test("new task defaults to downloads without account and local publishing exposes metadata", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await expect(page.getByRole("tab", { name: "下载素材" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "加入下载队列" }),
  ).toBeDisabled();
  await dialog.getByLabel("素材链接").fill("https://example.com/video.mp4");
  await dialog.getByRole("combobox", { name: "素材库", exact: true }).click();
  await page.getByRole("option", { name: "示例素材库" }).click();
  await expect(
    dialog.getByRole("button", { name: "加入下载队列" }),
  ).toBeEnabled();
  await page.screenshot({ path: "../output/download-task.png" });
  await page.getByRole("tab", { name: "同步投稿" }).click();
  await expect(dialog.getByLabel("YouTube 视频链接")).toBeVisible();
  await page.getByRole("button", { name: "本地视频", exact: true }).click();
  await expect(dialog.getByLabel("YouTube 视频链接")).toHaveCount(0);
  await dialog.getByLabel("视频文件路径").fill("C:\\Videos\\local.mp4");
  await dialog.getByLabel("标题", { exact: true }).fill("自己填写的标题");
  await dialog.getByLabel("简介", { exact: true }).fill("自己填写的简介");
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await dialog.getByRole("checkbox", { name: /我拥有该视频/ }).check();
  await expect(
    dialog.getByRole("button", { name: "加入队列", exact: true }),
  ).toBeEnabled();
  await expect(dialog).toContainText("投稿成功后保留源视频和任务工作目录");
  await page.screenshot({ path: "../output/local-publish-task.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const language of [
  { choice: "English", nav: "Materials", tab: "Download materials" },
  { choice: "繁體中文", nav: "素材管理", tab: "下載素材" },
]) {
  test(`material interface localizes to ${language.choice}`, async ({
    page,
  }) => {
    await page.goto("/?preview");
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("combobox", { name: "界面语言" }).click();
    await page.getByRole("option", { name: language.choice }).click();
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect(
      page.getByRole("button", { name: language.nav, exact: true }),
    ).toBeVisible();
    await page.locator("nav .nav-item").first().click();
    await page.locator(".page-heading button.primary").click();
    await expect(page.getByRole("tab", { name: language.tab })).toBeVisible();
  });
}
