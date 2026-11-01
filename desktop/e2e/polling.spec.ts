import { test, expect, type Page } from "@playwright/test";

async function countedBridge(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    w.isTauri = true;
    w.pollCalls = [];
    w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    w.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return 1; },
      async invoke(command: string, args: any = {}) {
        if (command === "backend_request") {
          w.pollCalls.push(args.method);
          if (args.method === "logs.tail" && w.holdLogs) {
            await new Promise<void>((resolve) => { w.releaseLogs = resolve; });
          }
          if (args.method === "logs.tail") {
            return { items: [{ time: "12:00:00", level: "INFO", message: "Current task log" }] };
          }
          const preview = await import(/* @vite-ignore */ "/src/preview.ts");
          return preview.request(args.method, args.params);
        }
        if (command === "plugin:image|new") return 1;
        return null;
      },
    };
  });
  await page.clock.install();
  await page.goto("/?populated&accounts=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator("button.task-row")).toHaveCount(3, { timeout: 30000 });
}

const calls = (page: Page, method: string) => page.evaluate(
  (method) => (window as any).pollCalls.filter((value: string) => value === method).length,
  method,
);

async function visibility(page: Page, hidden: boolean) {
  await page.evaluate((hidden) => {
    Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
}

test("task polling pauses while hidden and refreshes immediately on return", async ({ page }) => {
  await countedBridge(page);
  const initial = await calls(page, "tasks.list");
  await page.clock.runFor(1600);
  await expect.poll(() => calls(page, "tasks.list")).toBeGreaterThan(initial);
  await visibility(page, true);
  const hidden = await calls(page, "tasks.list");
  await page.clock.runFor(6000);
  expect(await calls(page, "tasks.list")).toBe(hidden);
  await visibility(page, false);
  await expect.poll(() => calls(page, "tasks.list")).toBe(hidden + 1);
  await page.clock.runFor(1600);
  await expect.poll(() => calls(page, "tasks.list")).toBe(hidden + 2);
});

test("slow log reads stay serial and stop when hidden or leaving the log tab", async ({ page }) => {
  await countedBridge(page);
  await page.locator("button.task-row").first().click();
  await expect(page.getByRole("dialog", { name: "任务详情" })).toBeVisible();
  await page.evaluate(() => { (window as any).holdLogs = true; });
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  await expect.poll(() => calls(page, "logs.tail")).toBe(1);
  await page.clock.runFor(6000);
  expect(await calls(page, "logs.tail")).toBe(1);
  // Returning while a request is pending should queue one fresh read, not
  // overlap it or wait another complete polling interval after it settles.
  await visibility(page, true);
  await visibility(page, false);
  expect(await calls(page, "logs.tail")).toBe(1);
  await page.evaluate(() => {
    (window as any).holdLogs = false;
    (window as any).releaseLogs();
  });
  await expect(page.locator(".log-view")).toContainText("Current task log");
  await expect.poll(() => calls(page, "logs.tail")).toBe(2);
  await page.clock.runFor(1600);
  await expect.poll(() => calls(page, "logs.tail")).toBe(3);
  await visibility(page, true);
  await page.clock.runFor(6000);
  expect(await calls(page, "logs.tail")).toBe(3);
  await visibility(page, false);
  await expect.poll(() => calls(page, "logs.tail")).toBe(4);
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await page.clock.runFor(6000);
  expect(await calls(page, "logs.tail")).toBe(4);
});
