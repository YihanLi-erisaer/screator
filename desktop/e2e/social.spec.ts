import { test, expect } from "@playwright/test";

test("social opt-ins require connected accounts and TikTok rejects automatic mode", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建任务/ }).first().click();
  await page.getByRole("tab", { name: "一键上传", exact: true }).click();
  for (const name of [/同步上传 X/, /同步上传 Facebook/, /同步上传 TikTok/]) {
    await expect(page.getByRole("checkbox", { name })).toBeDisabled();
  }
  await page.goto("/?preview&accounts=1&social=1");
  await page.getByRole("button", { name: /新建任务/ }).first().click();
  await page.getByRole("tab", { name: "一键上传", exact: true }).click();
  const panel = page.getByRole("tabpanel", { name: "视频上传", exact: true });
  const tiktok = panel.getByRole("checkbox", { name: /同步上传 TikTok/ });
  await expect(tiktok).toBeEnabled();
  await panel.getByRole("radio", { name: /自动投稿/ }).check();
  await expect(tiktok).toBeDisabled();
  await expect(panel.getByRole("checkbox", { name: /同步上传 X/ })).toBeEnabled();
  await expect(panel.getByRole("checkbox", { name: /同步上传 Facebook/ })).toBeEnabled();
  await page.screenshot({ path: "/private/tmp/screator-social-opt-ins.png" });
});

for (const platform of ["twitter", "facebook"]) {
  test(`${platform} preview saves its separate title before confirmation`, async ({ page }) => {
    await page.goto(`/?preview&accounts=1&social=1&populated&socialPublication=${platform}`);
    await page.getByRole("button", { name: /用更少的工具/ }).click();
    const dialog = page.getByRole("dialog", { name: "任务详情", exact: true });
    const title = dialog.getByLabel("平台标题或视频文案");
    await title.fill("独立平台标题");
    const confirm = dialog.getByRole("button", { name: "确认投稿未完成目标", exact: true });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole("button", { name: "保存平台投稿信息", exact: true }).click();
    await expect(confirm).toBeEnabled();
    await expect(title).toHaveValue("独立平台标题");
  });
}

test("TikTok preview requires manual visibility and consent and honors live permissions", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/?preview&accounts=1&social=1&populated&socialPublication=tiktok");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情", exact: true });
  const privacy = dialog.getByRole("combobox", { name: "TikTok 可见性", exact: true });
  await expect(privacy).toContainText("请手动选择可见性");
  await expect(dialog.getByRole("checkbox", { name: "允许评论", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("checkbox", { name: "允许合拍", exact: true })).not.toBeChecked();
  const confirm = dialog.getByRole("button", { name: "确认投稿未完成目标", exact: true });
  await expect(confirm).toBeDisabled();
  await privacy.click();
  await page.getByRole("option", { name: "仅自己", exact: true }).click();
  await dialog.getByRole("checkbox", { name: "这是我创作的原创内容", exact: true }).check();
  await dialog.getByRole("button", { name: "保存平台投稿信息", exact: true }).click();
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("checkbox", { name: /发布即表示我同意 TikTok 音乐使用确认/ }).check();
  await dialog.getByRole("button", { name: "保存平台投稿信息", exact: true }).click();
  await expect(confirm).toBeEnabled();
  await dialog.getByRole("checkbox", { name: "允许合拍", exact: true }).check();
  await expect(dialog.getByRole("checkbox", { name: /发布即表示我同意 TikTok 音乐使用确认/ })).not.toBeChecked();
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "此视频包含推广内容", exact: true }).check();
  await dialog.getByRole("checkbox", { name: "推广第三方品牌（付费合作）", exact: true }).check();
  await expect(dialog.getByText("品牌合作内容不能选择仅自己可见，请重新选择可见性。", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "保存平台投稿信息", exact: true })).toBeDisabled();
  await privacy.click();
  await page.getByRole("option", { name: "所有人", exact: true }).click();
  await dialog.getByRole("checkbox", { name: /发布即表示我同意 TikTok 品牌内容政策和音乐使用确认/ }).check();
  await dialog.getByRole("button", { name: "保存平台投稿信息", exact: true }).click();
  await expect(confirm).toBeEnabled();
  await dialog.getByRole("button", { name: "预览投稿视频", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/private/tmp/screator-tiktok-preview.png" });
});

test("account connections show X, Facebook Page and TikTok credential panels", async ({ page }) => {
  await page.goto("/?preview&accounts=1&social=1");
  await page.getByRole("button", { name: "账号与连接", exact: true }).click();
  for (const name of ["X（Twitter） · 单账号同步投稿", "Facebook Page · 单账号同步投稿", "TikTok · 单账号同步投稿"]) {
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  }
  await expect(page.getByLabel("Facebook Page ID", { exact: true })).toBeVisible();
  await page.getByRole("heading", { name: "Facebook Page · 单账号同步投稿", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/private/tmp/screator-social-accounts.png" });
});
