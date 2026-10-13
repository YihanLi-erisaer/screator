import { test, expect, type Page, type Locator } from "@playwright/test";

const cancelledMessage = "任务已取消，已保留可恢复的素材。";

async function cancelledTaskBridge(page: Page, message: string) {
  await page.addInitScript(
    ({ message }) => {
      const w = window as any;
      w.isTauri = true;
      w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
      w.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: "main" } },
        transformCallback() {
          return 1;
        },
        async invoke(command: string, args: any = {}) {
          if (command === "backend_request") {
            if (args.method === "tasks.retry")
              throw new Error("下载队列暂时不可用。");
            const preview = await import(/* @vite-ignore */ "/src/preview.ts");
            const result = await preview.request(args.method, args.params);
            const fixture = (task: any) => ({
              ...task,
              status: "cancelled",
              error: message,
            });
            if (args.method === "tasks.get") return fixture(result);
            if (args.method === "tasks.list")
              return { ...result, items: result.items.map(fixture) };
            return result;
          }
          if (command === "plugin:image|new") return 1;
          return null;
        },
      };
    },
    { message },
  );
}

async function expectCloseButtonReachable(toast: Locator) {
  const close = toast.getByRole("button", { name: "关闭错误提示" });
  await expect
    .poll(() =>
      close.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return element.contains(
          document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          ),
        );
      }),
    )
    .toBe(true);
}

for (const theme of ["dark", "light"] as const) {
  for (const length of ["short", "long"] as const) {
    test(`${theme} ${length} errors stay fully visible above a scrolling task dialog`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize({
        width: length === "long" ? 480 : 1280,
        height: 680,
      });
      const message =
        length === "short"
          ? cancelledMessage
          : "保存素材失败，请检查目录权限。\n".repeat(40) +
            "https://example.com/" +
            "long-path-".repeat(40);
      await cancelledTaskBridge(page, message);
      await page.goto("/?populated&accounts=1");
      if (theme === "light")
        await page.getByRole("button", { name: "切换明暗主题" }).click();
      await page.clock.install();
      await page.locator("button.task-row").first().click();
      const detail = page.getByRole("dialog", {
        name: "任务详情",
        exact: true,
      });
      const toast = detail.getByRole("alert");
      await expect(toast).toContainText(message);
      await expectCloseButtonReachable(toast);
      const bounds = await toast.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return {
          left: box.left,
          top: box.top,
          right: box.right,
          bottom: box.bottom,
          width: innerWidth,
          height: innerHeight,
        };
      });
      expect(bounds.left).toBeGreaterThanOrEqual(0);
      expect(bounds.top).toBeGreaterThanOrEqual(0);
      expect(bounds.right).toBeLessThanOrEqual(bounds.width);
      expect(bounds.bottom).toBeLessThanOrEqual(bounds.height);
      await page.screenshot({
        path: `../output/error-toast-${theme}-${length}.png`,
      });
      await detail.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      if (length === "long") {
        const text = toast.locator(":scope > span");
        expect(
          await text.evaluate(
            (element) => element.scrollHeight > element.clientHeight,
          ),
        ).toBe(true);
        await text.evaluate((element) => {
          element.scrollTop = element.scrollHeight;
        });
      }
      await expectCloseButtonReachable(toast);
      await toast.getByRole("button", { name: "关闭错误提示" }).click();
      await expect(toast).toHaveCount(0);
    });
  }
}

test("one toast viewport follows nested dialogs and remains usable after they close", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await cancelledTaskBridge(page, cancelledMessage);
  await page.goto("/?populated&accounts=1");
  await page.clock.install({ time: new Date("2026-10-13T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-13T00:00:01Z"));
  await page.locator("button.task-row").first().click();
  const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
  await expect(detail.getByRole("alert")).toBeVisible();
  await detail.getByRole("button", { name: "继续任务", exact: true }).click();
  const choice = page.getByRole("dialog", {
    name: "选择继续任务的投稿模式",
    exact: true,
  });
  await expect(choice.getByRole("alert")).toContainText(cancelledMessage);
  await expect(page.locator(".toast-viewport")).toHaveCount(1);
  await choice.getByRole("button", { name: "继续任务", exact: true }).click();
  const error = choice
    .getByRole("alert")
    .filter({ hasText: "下载队列暂时不可用。" });
  await expectCloseButtonReachable(error);
  await error.getByRole("button", { name: "关闭错误提示" }).click();
  await page.clock.runFor(1);
  await choice.getByRole("button", { name: "取消", exact: true }).click();
  await expect(detail.getByRole("alert")).toContainText(cancelledMessage);
  await detail.getByRole("button", { name: "关闭对话框", exact: true }).click();
  const toast = page.getByRole("alert");
  await expect(toast).toContainText(cancelledMessage);
  await expect(page.locator(".toast-viewport")).toHaveCount(1);
  await expectCloseButtonReachable(toast);
  await toast.getByRole("button", { name: "关闭错误提示" }).click();
  await page.clock.runFor(1);
  await expect(toast).toHaveCount(0);
});

test("toasts follow a dialog opened later even when it comes first in DOM order", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await cancelledTaskBridge(page, cancelledMessage);
  await page.goto("/?populated&accounts=1");
  await page.clock.install({ time: new Date("2026-10-13T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-13T00:00:01Z"));
  await page.locator("button.task-row").first().click();
  const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
  await expect(detail.getByRole("alert")).toBeVisible();
  await page.keyboard.press("Control+n");
  const create = page.getByRole("dialog", {
    name: "新建投稿任务",
    exact: true,
  });
  const toast = create.getByRole("alert");
  await expect(toast).toContainText(cancelledMessage);
  await expectCloseButtonReachable(toast);
  await expect(page.locator(".toast-viewport")).toHaveCount(1);
  await create.getByRole("button", { name: "关闭对话框", exact: true }).click();
  await expect(detail.getByRole("alert")).toBeVisible();
  await expectCloseButtonReachable(detail.getByRole("alert"));
});
