import { test, expect } from "@playwright/test";

test("resources show app totals, tools, refresh, pause and resume", async ({ page }) => {
  await page.clock.install();
  await page.goto("/?preview&resourceWarmup");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const monitor = page.getByRole("region", { name: "应用资源监控" });
  await expect(monitor).toBeVisible();
  await expect(monitor.locator(".resource-metrics strong")).toHaveText(["—", "1.0 GB", "5"]);
  await expect(monitor.getByText("部分进程的 CPU 正在采样或暂不可读，当前合计仅包含已取得的数据。")).toBeVisible();
  await page.clock.runFor(2100);
  await expect(monitor.locator(".resource-metrics strong")).toHaveText(["68.5%", "1.0 GB", "5"]);
  await monitor.getByText("查看进程明细", { exact: true }).click();
  await expect(monitor.locator("tbody tr")).toHaveCount(5);
  await expect(monitor.locator("tbody")).toContainText("ffmpeg.exe");
  await expect(monitor.locator("tbody")).toContainText("msedgewebview2.exe");
  await expect(monitor.locator("tbody")).toContainText("ollama.exe");
  await monitor.getByRole("button", { name: "暂停刷新" }).click();
  const paused = await monitor.locator("time").getAttribute("datetime");
  await page.clock.runFor(5000);
  await expect(monitor.locator("time")).toHaveAttribute("datetime", paused!);
  await monitor.getByRole("button", { name: "继续刷新" }).click();
  await expect(monitor.locator("time")).not.toHaveAttribute("datetime", paused!);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const hiddenSample = await monitor.locator("time").getAttribute("datetime");
  await page.clock.runFor(5000);
  await expect(monitor.locator("time")).toHaveAttribute("datetime", hiddenSample!);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(monitor.locator("time")).not.toHaveAttribute("datetime", hiddenSample!);
  await page.screenshot({ path: "test-results/resource-monitor.png", fullPage: true });
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await expect(monitor).toHaveCount(0);
  for (const width of [1024, 600]) {
    await page.setViewportSize({ width, height: 680 });
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await expect(monitor).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  }
});

test("resource monitor supports display language changes and preserves process names", async ({ page }) => {
  await page.goto("/?preview");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "界面语言" }).click();
  await page.getByRole("option", { name: "English" }).click();
  await page.getByRole("button", { name: "保存设置" }).click();
  const monitor = page.getByRole("region", { name: "App resource monitor" });
  await expect(monitor.getByRole("heading", { name: "App resource monitor" })).toBeVisible();
  await monitor.getByText("View process details", { exact: true }).click();
  await expect(monitor.locator("tbody")).toContainText("ffmpeg.exe");
  await expect(monitor.getByRole("columnheader", { name: "Memory" })).toBeVisible();
});

test("monitor retries failed reads without presenting zero usage", async ({ page }) => {
  await page.clock.install();
  await page.goto("/?preview&resourceError");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const monitor = page.getByRole("region", { name: "应用资源监控" });
  await expect(monitor.getByRole("alert")).toBeVisible();
  await expect(monitor.locator(".resource-metrics")).toHaveCount(0);
  await page.clock.runFor(4100);
  await expect(monitor.locator(".resource-metrics strong")).toHaveText(["68.5%", "1.0 GB", "5"]);
  await expect(monitor.getByRole("alert")).toHaveCount(0);
});
