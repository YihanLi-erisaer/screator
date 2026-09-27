import { test, expect } from "@playwright/test";

test("missing AcFun challenge offers a direct recovery action instead of a dead end", async ({ page }) => {
  await page.route("https://passport.kuaishou.com/**", route => route.fulfill({ contentType: "text/html", body: "Official verification fixture" }));
  await page.goto("/?preview&accounts=1&acfun=1&populated&acfunVerification&acfunVerificationMissing");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  await page.getByRole("button", { name: "完成 AcFun 安全验证" }).click();
  await expect(page.getByText("本次验证入口未保存或来自旧版后台。", { exact: true })).toBeVisible();
  await expect(page.getByText(/若平台已放行，本次重试将直接投稿/)).toBeVisible();
  await page.getByRole("button", { name: "重新获取验证入口并继续投稿" }).click();
  await page.getByRole("button", { name: "完成 AcFun 安全验证" }).click();
  await expect(page.getByRole("dialog", { name: "AcFun 官方安全验证", exact: true })).toBeVisible();
});

for (const host of ["passport.kuaishou.com", "app.m.kuaishou.com"]) {
test(`AcFun security verification accepts only the official frame at ${host}`, async ({ page }) => {
  // Offline fixture only: no real challenge or publication is requested.
  await page.route(`https://${host}/**`, (route) => route.fulfill({
    contentType: "text/html; charset=utf-8", body: `<meta charset="utf-8"><button onclick='parent.postMessage(JSON.stringify({msgType:"RESULT",msg:{result:1,type:"captcha",token:"preview-proof"}}),"*")'>测试验证通过</button>`,
  }));
  await page.goto("/?preview&accounts=1&acfun=1&populated&acfunVerification" + (host === "app.m.kuaishou.com" ? "&acfunMobileVerification" : ""));
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  await page.getByRole("button", { name: "完成 AcFun 安全验证" }).click();
  const dialog = page.getByRole("dialog", { name: "AcFun 官方安全验证", exact: true });
  await expect(dialog).toBeVisible();
  await page.evaluate((origin) => {
    const data = JSON.stringify({ msgType: "RESULT", msg: { result: 1, type: "captcha", token: "preview-proof" } });
    const frame = document.querySelector("iframe")!;
    // Wrong origin and wrong source must both be ignored.
    window.dispatchEvent(new MessageEvent("message", { data, origin: "https://evil.example", source: frame.contentWindow }));
    window.dispatchEvent(new MessageEvent("message", { data, origin, source: window }));
  }, `https://${host}`);
  await expect(dialog).toBeVisible();
  await page.frameLocator('iframe[title="AcFun 官方安全验证"]').getByRole("button", { name: "测试验证通过" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "确认投稿未完成目标" })).toBeEnabled();
  await expect(page.getByText("AcFun · 待预览", { exact: true })).toBeVisible();
});
}

test("AcFun opt-in supports preview and automatic submission", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建任务/ }).first().click();
  let dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("checkbox", { name: /同步上传 AcFun/ })).toBeDisabled();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();

  await page.goto("/?preview&accounts=1&acfun=1");
  await expect(page.getByText("AcFun 独立上传")).toBeVisible();
  await page.getByRole("button", { name: /新建任务/ }).first().click();
  dialog = page.getByRole("dialog");
  const sync = dialog.getByRole("checkbox", { name: /同步上传 AcFun/ });
  await expect(sync).toBeEnabled();
  await expect(sync).not.toBeChecked();
  await sync.check();
  await expect(dialog.getByText(/AcFun 使用独立队列/)).toBeVisible();
  await dialog.getByRole("radio", { name: /自动投稿/ }).check();
  await expect(sync).toBeEnabled();
  await expect(sync).toBeChecked();
  await expect(dialog.getByText(/素材准备好后自动投稿/)).toBeVisible();
});

test("AcFun settings select real video subchannels by name", async ({ page }) => {
  await page.goto("/?preview&accounts=1&acfun=1");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const channel = page.getByLabel("AcFun 默认分区（自动投稿必填）");
  await expect(channel).toBeEnabled();
  await expect(channel.locator('option[value="1"]')).toHaveCount(0);
  await expect(channel.locator('option[value="196"]')).toHaveText("影视 / 纪录片·短片（196）");
  await channel.selectOption("196");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("设置已保存。", { exact: true })).toBeVisible();
  await expect(channel).toHaveValue("196");
});
