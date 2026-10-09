import { test, expect, type Page } from "@playwright/test";

async function nativeBridge(page: Page, stallPrepare = false) {
  await page.addInitScript(({ stallPrepare }) => {
    const w = window as any;
    const callbacks = new Map<number, (event: unknown) => void>();
    const listeners = new Map<string, number>();
    let next = 1;
    w.isTauri = true;
    w.shutdownCalls = [];
    w.shutdownState = { closing: true, ready: false, pending_task_ids: ["running"], inflight_task_ids: [], stages: ["download"] };
    w.emitNative = (event: string, payload: unknown = {}) => {
      const callback = callbacks.get(listeners.get(event)!);
      callback?.({ event, id: 0, payload });
    };
    w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    w.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback(callback: (event: unknown) => void) { const id = next++; callbacks.set(id, callback); return id; },
      async invoke(command: string, args: any = {}) {
        if (command === "plugin:event|listen") { listeners.set(args.event, args.handler); return next++; }
        if (command === "backend_request") {
          w.shutdownCalls.push(args.method);
          if (args.method === "system.prepare_shutdown") {
            if (stallPrepare) return new Promise(() => {});
            return structuredClone(w.shutdownState);
          }
          if (args.method === "system.shutdown_status") {
            if (stallPrepare) return new Promise(() => {});
            return structuredClone(w.shutdownState);
          }
          // Reuse source-backed preview fixtures for unrelated startup requests.
          const preview = await import(/* @vite-ignore */ "/src/preview.ts");
          return preview.request(args.method, args.params);
        }
        if (command === "finish_close" || command === "force_close") {
          w.shutdownCalls.push(command);
          if (command === "finish_close" && w.failFinalizeOnce) {
            w.failFinalizeOnce = false;
            throw new Error("Transient finalization failure");
          }
          return;
        }
        if (command === "plugin:image|new") return 1;
        return null;
      },
    };
  }, { stallPrepare });
  await page.goto("/?accounts=1&populated");
  await expect(page.getByRole("button", { name: /新建任务/ })).toBeEnabled();
  await expect.poll(() => page.evaluate(() => (window as any).shutdownCalls.includes("tasks.list"))).toBeTruthy();
  await page.evaluate(() => (window as any).emitNative("app-close-requested"));
  await page.getByRole("button", { name: "退出应用", exact: true }).click();
}

test("force exit stays available while prepare shutdown is stalled", async ({ page }) => {
  await nativeBridge(page, true);
  await expect(page.getByText("正在通知后台停止任务并保存状态…")).toBeVisible();
  const force = page.getByRole("button", { name: "停止投稿并退出" });
  await expect(force).toBeEnabled();
  await force.click();
  await expect.poll(() => page.evaluate(() => (window as any).shutdownCalls.includes("force_close"))).toBeTruthy();
});

test("ready event exits immediately and duplicate events only finalize once", async ({ page }) => {
  await nativeBridge(page);
  await expect(page.getByText("正在停止下载、校验或素材准备。")).toBeVisible();
  await page.evaluate(() => {
    const w = window as any;
    w.shutdownState = { closing: true, ready: true, pending_task_ids: [], inflight_task_ids: [] };
    w.emitNative("backend-event", { event: "system.shutdown_status", payload: w.shutdownState });
    w.emitNative("backend-event", { event: "system.shutdown_status", payload: w.shutdownState });
  });
  await expect.poll(() => page.evaluate(() => (window as any).shutdownCalls.filter((method: string) => method === "finish_close").length)).toBe(1);
  await expect(page.getByText("正在释放后台资源并退出…")).toBeVisible();
});

test("shutdown stops task refresh and ignores unrelated backend notifications", async ({ page }) => {
  await nativeBridge(page);
  const before = await page.evaluate(() => (window as any).shutdownCalls.filter((method: string) => method === "tasks.list").length);
  await page.evaluate(() => {
    const w = window as any;
    w.emitNative("backend-event", { event: "accounts.changed", payload: {} });
    w.emitNative("backend-event", { event: "publication.changed", payload: { task_id: "running" } });
  });
  await page.waitForTimeout(1700);
  const calls = await page.evaluate(() => (window as any).shutdownCalls as string[]);
  expect(calls.filter((method) => method === "tasks.list").length).toBe(before);
  const after = calls.slice(calls.indexOf("system.prepare_shutdown") + 1);
  expect(after.every((method) => method === "system.shutdown_status")).toBeTruthy();
});

test("a failed final close can retry through the fallback status check", async ({ page }) => {
  await nativeBridge(page);
  await page.evaluate(() => {
    const w = window as any;
    w.failFinalizeOnce = true;
    w.shutdownState = { closing: true, ready: true, pending_task_ids: [], inflight_task_ids: [] };
    w.emitNative("backend-event", { event: "system.shutdown_status", payload: w.shutdownState });
  });
  await expect.poll(() => page.evaluate(() => (window as any).shutdownCalls.filter((method: string) => method === "finish_close").length)).toBe(2);
});
