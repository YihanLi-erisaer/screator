import { test, expect, type Page } from "@playwright/test";

async function retryBridge(page: Page, status: string, sourceKind = "youtube") {
  await page.addInitScript(({ status, sourceKind }) => {
    const w = window as any;
    w.isTauri = true;
    w.retryCalls = [];
    w.retryStatus = status;
    w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    w.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return 1; },
      async invoke(command: string, args: any = {}) {
        if (command === "backend_request") {
          if (args.method === "tasks.retry") {
            w.retryCalls.push(args.params);
            if (w.retryError) throw new Error(w.retryError);
            w.retryStatus = "queued_download";
            return { queued: true };
          }
          const preview = await import(/* @vite-ignore */ "/src/preview.ts");
          const result = await preview.request(args.method, args.params);
          const fixture = (task: any) => ({
            ...task,
            source_kind: sourceKind,
            status: w.retryStatus,
            snapshot: { mode: "auto", settings: {} },
          });
          if (args.method === "tasks.list") return { ...result, items: [fixture(result.items[0])] };
          if (args.method === "tasks.get") return fixture(result);
          return result;
        }
        if (command === "plugin:image|new") return 1;
        return null;
      },
    };
  }, { status, sourceKind });
  await page.goto("/?populated&accounts=1");
  await page.locator("button.task-row").first().click();
}

for (const status of ["cancelled", "failed"]) {
  for (const mode of ["auto", "preview"]) {
    test(`${status} automatic task resumes with chosen ${mode} mode`, async ({ page }) => {
      await retryBridge(page, status);
      const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
      await detail.getByRole("button", { name: "继续任务", exact: true }).click();
      const choice = page.getByRole("dialog", { name: "选择继续任务的投稿模式" });
      await expect(choice).toBeVisible();
      expect(await page.evaluate(() => (window as any).retryCalls.length)).toBe(0);
      await choice.getByRole("radio", { name: mode === "auto" ? /自动投稿/ : /预览/ }).check();
      await choice.getByRole("button", { name: "继续任务", exact: true }).click();
      await expect(choice).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).retryCalls[0])).toMatchObject({
        task_id: "abcdefghijk", mode, use_current_translation_settings: false,
      });
    });
  }
}

test("retry with current translation settings also chooses a submission mode", async ({ page }) => {
  await retryBridge(page, "failed");
  await page.getByRole("button", { name: "使用当前翻译设置重试" }).click();
  const choice = page.getByRole("dialog", { name: "选择继续任务的投稿模式" });
  await choice.getByRole("radio", { name: /自动投稿/ }).check();
  await choice.getByRole("button", { name: "继续任务", exact: true }).click();
  await expect(choice).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).retryCalls[0])).toMatchObject({
    mode: "auto", use_current_translation_settings: true,
  });
});

test("dismissing retry choices leaves the local task cancelled", async ({ page }) => {
  await retryBridge(page, "cancelled", "local");
  const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
  await detail.getByRole("button", { name: "继续任务", exact: true }).click();
  const choice = page.getByRole("dialog", { name: "选择继续任务的投稿模式" });
  await choice.getByRole("button", { name: "取消", exact: true }).click();
  await expect(choice).toHaveCount(0);
  await expect(detail).toBeVisible();
  await detail.getByRole("button", { name: "继续任务", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(choice).toHaveCount(0);
  await expect(detail).toBeVisible();
  expect(await page.evaluate(() => (window as any).retryCalls.length)).toBe(0);
});

test("a rejected automatic retry keeps choices open to switch to preview", async ({ page }) => {
  await retryBridge(page, "failed", "image_post");
  await page.evaluate(() => { (window as any).retryError = "请使用预览模式。"; });
  await page.getByRole("dialog", { name: "任务详情", exact: true })
    .getByRole("button", { name: "继续任务", exact: true }).click();
  const choice = page.getByRole("dialog", { name: "选择继续任务的投稿模式" });
  await choice.getByRole("button", { name: "继续任务", exact: true }).click();
  await expect(choice.getByRole("alert")).toContainText("请使用预览模式。");
  await page.evaluate(() => { (window as any).retryError = ""; });
  await choice.getByRole("radio", { name: /预览/ }).check();
  await choice.getByRole("button", { name: "继续任务", exact: true }).click();
  await expect(choice).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).retryCalls);
  expect(calls.map((call: any) => call.mode)).toEqual(["auto", "preview"]);
  expect(calls[0].operation_id).not.toBe(calls[1].operation_id);
});
