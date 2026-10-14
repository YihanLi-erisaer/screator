import { test, expect } from "@playwright/test";

test("batch buttons sit left of new task and cancel tasks outside search and pagination", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=2&taskFilters");
  const cancel = page.getByRole("button", { name: "取消所有未完成", exact: true });
  const retry = page.getByRole("button", { name: "重试所有未完成", exact: true });
  const create = page.getByRole("button", { name: /新建投稿任务/ });
  await expect(cancel).toBeEnabled();
  const positions = await Promise.all([cancel.boundingBox(), retry.boundingBox(), create.boundingBox()]);
  expect(positions[0]!.x).toBeLessThan(positions[1]!.x);
  expect(positions[1]!.x).toBeLessThan(positions[2]!.x);
  await page.screenshot({ path: "/private/tmp/screator-task-batch-buttons.png", animations: "disabled" });
  await page.getByRole("textbox", { name: "搜索任务" }).fill("bilibili");
  await expect(page.locator("button.task-row")).toHaveCount(3);
  await cancel.click();
  await expect(page.getByRole("status").filter({ hasText: "已请求取消 16 个任务，跳过 8 个任务。" })).toBeVisible();
  await page.getByRole("textbox", { name: "搜索任务" }).fill("");
  await expect(page.locator("button.task-row")).toHaveCount(20);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("button.task-row")).toHaveCount(4);
  await expect(page.locator("button.task-row").filter({ hasText: "已取消" })).toHaveCount(3);
});

test("batch retry resumes cancelled tasks across all platform tabs", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=2&taskFilters");
  await page.getByRole("button", { name: "取消所有未完成", exact: true }).click();
  await expect(page.getByRole("button", { name: "重试所有未完成", exact: true })).toBeEnabled();
  await page.getByRole("tab", { name: "AcFun", exact: true }).click();
  await expect(page.locator("button.task-row")).toHaveCount(3);
  await page.getByRole("button", { name: "重试所有未完成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已恢复 16 个任务，跳过 8 个任务。" })).toBeVisible();
  await expect(page.locator("button.task-row").filter({ hasText: "等待下载" })).toHaveCount(2);
  await expect(page.locator("button.task-row").filter({ hasText: "下载完成" })).toHaveCount(1);
});

test("empty center disables batch actions and other pages hide them", async ({ page }) => {
  await page.goto("/?preview&accounts=1");
  await expect(page.getByRole("button", { name: "取消所有未完成", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "重试所有未完成", exact: true })).toBeDisabled();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "素材管理", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消所有未完成", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /新建下载任务/ })).toBeVisible();
});

test("batch action blocks duplicate clicks and reports individual failures", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as any;
    w.isTauri = true;
    w.batchCalls = [];
    w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    w.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return 1; },
      async invoke(command: string, args: any = {}) {
        if (command === "backend_request") {
          if (args.method === "tasks.cancel_all") {
            w.batchCalls.push(args.params);
            return new Promise((resolve) => { w.releaseBatch = () => resolve({
              processed: 2, skipped: 1,
              errors: [{ task_id: "protected-task", error: "正在提交，请等待上传结束并核对结果。" }],
            }); });
          }
          const preview = await import(/* @vite-ignore */ "/src/preview.ts");
          return preview.request(args.method, args.params);
        }
        if (command === "plugin:image|new") return 1;
        return null;
      },
    };
  });
  await page.goto("/?populated&accounts=1");
  await page.getByRole("button", { name: "取消所有未完成", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消所有未完成", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "重试所有未完成", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: /新建投稿任务/ })).toBeDisabled();
  const calls = await page.evaluate(() => (window as any).batchCalls);
  expect(calls).toHaveLength(1);
  expect(Object.keys(calls[0])).toEqual(["operation_id"]);
  await page.evaluate(() => (window as any).releaseBatch());
  await expect(page.getByRole("alert")).toContainText("已请求取消 2 个任务");
  await expect(page.getByRole("alert")).toContainText("protected-task");
  await expect(page.getByRole("button", { name: "取消所有未完成", exact: true })).toBeEnabled();
});
