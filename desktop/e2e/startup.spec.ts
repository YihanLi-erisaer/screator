import { test, expect } from "@playwright/test";

test("task center becomes usable while translation service detection is pending", async ({ page }) => {
  await page.goto("/?preview&accounts=1&populated&slowSettingsStatus=2500");
  await expect(page.locator(".task-row")).toHaveCount(3);
  await expect(page.locator(".setup-banner")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^新建任务/ })).toBeEnabled();
  await expect(page.locator(".setup-banner")).toBeVisible();
});

test("an older readiness response cannot overwrite refreshed credentials", async ({ page }) => {
  await page.goto("/?preview&slowSettingsStatus=2500");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "通用设置" })).toBeVisible();
  await page.getByLabel("DeepL API 密钥").fill("test-key");
  await page.getByRole("button", { name: "保存密钥", exact: true }).click();
  await expect(page.locator(".translation-panel .section-title")).toContainText("已就绪");
  // Wait for the old response to arrive before checking the new credential state.
  await expect.poll(() => page.evaluate(() => performance.now())).toBeGreaterThan(3200);
  await expect(page.locator(".translation-panel .section-title")).toContainText("已就绪");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await expect(page.locator(".setup-banner")).toHaveCount(0);
});
