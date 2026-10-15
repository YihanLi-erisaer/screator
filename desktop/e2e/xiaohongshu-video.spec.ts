import { test, expect, type Page } from "@playwright/test";

async function openVideoForm(page: Page, connected = true) {
  await page.goto(`/?preview&accounts=1${connected ? "&xiaohongshu=1" : ""}`);
  await page.getByRole("button", { name: /新建投稿任务/ }).click();
  return page.getByRole("tabpanel", { name: "视频上传", exact: true });
}

test("video sync requires a connected Xiaohongshu account", async ({ page }) => {
  const panel = await openVideoForm(page, false);
  await expect(panel.getByRole("checkbox", { name: /同步上传小红书/ })).toBeDisabled();
  await expect(panel.getByText("请先到账号与连接登录小红书，登录后可选择同步上传。")).toBeVisible();
});

test("video sync retains the draft and blocks an oversized shared description", async ({ page }) => {
  const panel = await openVideoForm(page);
  await panel.getByRole("button", { name: "本地视频", exact: true }).click();
  await panel.getByLabel("视频文件路径").fill("/videos/source.mp4");
  await panel.getByLabel("标题", { exact: true }).fill("B站标题");
  await panel.getByRole("textbox", { name: "简介", exact: true }).fill("文".repeat(1001));
  await panel.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await panel.getByRole("checkbox", { name: /我拥有该视频/ }).check();
  const sync = panel.getByRole("checkbox", { name: /同步上传小红书/ });
  const queue = panel.getByRole("button", { name: "加入队列", exact: true });
  await expect(queue).toBeEnabled();
  await sync.check();
  await panel.getByLabel("小红书标题").fill("独立的视频标题");
  await expect(panel.getByLabel("小红书标题")).toHaveAttribute("maxlength", "20");
  await expect(panel.getByRole("textbox", { name: "简介", exact: true })).toHaveAttribute("maxlength", "1000");
  await expect(queue).toBeDisabled();
  await panel.getByRole("textbox", { name: "简介", exact: true }).fill("共享简介");
  await expect(queue).toBeEnabled();
  await sync.uncheck();
  await sync.check();
  await expect(panel.getByLabel("小红书标题")).toHaveValue("独立的视频标题");
  await page.screenshot({ path: "/private/tmp/xiaohongshu-video-sync.png" });
});

for (const source of ["local", "youtube"]) {
  for (const mode of ["preview", "auto"]) {
    test(`${source} ${mode} sends Xiaohongshu identity and independent title to the backend`, async ({ page }) => {
      await page.addInitScript(() => {
        const w = window as any;
        w.isTauri = true;
        w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
        w.__TAURI_INTERNALS__ = {
          metadata: { currentWindow: { label: "main" } },
          transformCallback() { return 1; },
          async invoke(command: string, args: any = {}) {
            if (command !== "backend_request") return null;
            if (args.method === "tasks.create") {
              w.createdTask = args.params;
              return { created: true };
            }
            const preview = await import(/* @vite-ignore */ "/src/preview.ts");
            return preview.request(args.method, args.params);
          },
        };
      });
      const panel = await openVideoForm(page);
      if (source === "local") {
        await panel.getByRole("button", { name: "本地视频", exact: true }).click();
        await panel.getByLabel("视频文件路径").fill("/videos/source.mp4");
        await panel.getByLabel("标题", { exact: true }).fill("B站标题");
      } else {
        await panel.getByLabel("YouTube 视频链接").fill("https://www.youtube.com/watch?v=abcdefghijk");
      }
      await panel.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
      await page.getByRole("option", { name: /UID 10001/ }).click();
      if (mode === "auto") await panel.getByRole("radio", { name: /自动投稿/ }).check();
      await panel.getByRole("checkbox", { name: /我拥有该视频/ }).check();
      await panel.getByRole("checkbox", { name: /同步上传小红书/ }).check();
      await expect(panel.getByRole("button", { name: "加入队列", exact: true })).toBeDisabled();
      await panel.getByLabel("小红书标题").fill("小红书视频标题");
      await panel.getByRole("button", { name: "加入队列", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "新建投稿任务" })).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).createdTask)).toMatchObject({
        mode, sync_xiaohongshu: true, xiaohongshu_account_id: "xhs-account-preview",
        xiaohongshu_binding_revision: 1, xiaohongshu_title: "小红书视频标题",
      });
    });
  }
}
