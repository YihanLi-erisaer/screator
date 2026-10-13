import { test, expect, type Page } from "@playwright/test";

async function nativePickerBridge(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    w.isTauri = true;
    w.fileSelection = "C:\\Videos\\custom-cover.png";
    w.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    w.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return 1; },
      async invoke(command: string, args: any = {}) {
        if (command === "plugin:dialog|open") {
          w.pickerOptions = args.options;
          return w.fileSelection;
        }
        if (command === "backend_request") {
          if (args.method === "materials.libraries" && w.delayLibraryReads) {
            await new Promise<void>(resolve => {
              w.pendingLibraryReads = [...(w.pendingLibraryReads ?? []), resolve];
            });
          }
          if (args.method === "materials.libraries" && w.libraryFixture) {
            return { items: w.libraryFixture, root: "示例素材目录" };
          }
          if (args.method === "materials.list" && w.libraryFixture && args.params.library_id !== "sample-library") {
            const library = w.libraryFixture.find((item: any) => item.id === args.params.library_id);
            return { items: [], path: library?.path ?? "" };
          }
          if (args.method === "materials.import") {
            w.importedMaterials = args.params;
            w.materialImportCalls = (w.materialImportCalls ?? 0) + 1;
            if (w.materialImportError) throw new Error(w.materialImportError);
          }
          if (args.method === "materials.create_library") w.createdLibrary = args.params;
          if (args.method === "materials.download") {
            w.downloadCalls = [...(w.downloadCalls ?? []), args.params];
            if (w.downloadError) throw new Error(w.downloadError);
            return { created: true };
          }
          if (args.method === "tasks.create") {
            w.createdTask = args.params;
            return { created: true };
          }
          const preview = await import(/* @vite-ignore */ "/src/preview.ts");
          return preview.request(args.method, args.params);
        }
        if (command === "plugin:image|new") return 1;
        return null;
      },
    };
  });
}

async function coverPickerBridge(page: Page) {
  await nativePickerBridge(page);
  await page.goto("/?accounts=1");
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  await page.getByRole("button", { name: "本地视频", exact: true }).click();
}

test("library selections close the menu and keep its width for short and long names", async ({ page }) => {
  await nativePickerBridge(page);
  const longName = "这是一个很长的素材库文件夹名称".repeat(12);
  await page.addInitScript(name => {
    (window as any).libraryFixture = [
      { id: "short-library", name: "短名", path: "/素材/短名" },
      { id: "long-library", name, path: `/素材/${name}` },
    ];
  }, longName);
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  const select = page.getByRole("combobox", { name: "素材库", exact: true });
  const menu = page.getByRole("listbox", { name: "素材库", exact: true });
  for (const width of [1280, 800, 480]) {
    await page.setViewportSize({ width, height: 800 });
    const initialWidth = (await select.boundingBox())!.width;
    for (const name of ["短名", longName, longName, "请选择素材库"]) {
      await select.click();
      await expect(menu).toBeVisible();
      expect(Math.abs((await menu.boundingBox())!.width - initialWidth)).toBeLessThan(1);
      await menu.getByRole("option", { name, exact: true }).click();
      await expect(select).toHaveAttribute("aria-expanded", "false");
      await expect(menu).toHaveCount(0);
      await expect(select).toContainText(name);
      expect(Math.abs((await select.boundingBox())!.width - initialWidth)).toBeLessThan(1);
    }
  }
  await page.mouse.move(0, 0);
  await select.focus();
  await select.press("ArrowDown");
  await select.press("Enter");
  await expect(select).toContainText("短名");
  await expect(select).toHaveAttribute("aria-expanded", "false");
});

test("materials restore the last library after navigation and reload and allow clearing it", async ({ page }) => {
  await page.goto("/?preview");
  const materials = page.getByRole("button", { name: "素材管理", exact: true });
  await materials.click();
  const select = page.getByRole("combobox", { name: "素材库", exact: true });
  await select.click();
  await page.getByRole("option", { name: "示例素材库", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(3);
  await page.getByRole("button", { name: "投稿中心", exact: true }).click();
  await materials.click();
  await expect(select).toContainText("示例素材库");
  await expect(select).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".material-card")).toHaveCount(3);
  await page.reload();
  await materials.click();
  await expect(select).toContainText("示例素材库");
  await expect(page.locator(".material-card")).toHaveCount(3);
  await select.click();
  await page.getByRole("option", { name: "请选择素材库", exact: true }).click();
  await page.getByRole("button", { name: "投稿中心", exact: true }).click();
  await materials.click();
  await expect(select).toContainText("请选择素材库");
  await expect(page.locator(".material-toolbar")).toHaveCount(0);
});

test("a remembered library that no longer exists is cleared", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("screator.materials.selectedLibrary", "missing-library");
  });
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "素材库", exact: true })).toContainText("请选择素材库");
  await expect(page.locator(".material-toolbar")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("screator.materials.selectedLibrary"))).toBeNull();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("local auto publishing sends the selected cover and supports clearing or cancelling the picker", async ({ page }) => {
  await coverPickerBridge(page);
  const dialog = page.getByRole("dialog", { name: "新建投稿任务" });
  const picker = dialog.getByRole("button", { name: "选择封面图片" });
  await picker.click();
  await expect(dialog.getByLabel("封面文件路径")).toHaveValue("C:\\Videos\\custom-cover.png");
  expect(await page.evaluate(() => (window as any).pickerOptions.filters[0].extensions)).toEqual(["jpg", "jpeg", "png", "webp", "bmp"]);
  await page.evaluate(() => { (window as any).fileSelection = null; });
  await picker.click();
  await expect(dialog.getByLabel("封面文件路径")).toHaveValue("C:\\Videos\\custom-cover.png");
  await dialog.getByRole("button", { name: "清除封面" }).click();
  await expect(dialog.getByLabel("封面文件路径")).toHaveValue("");
  await dialog.getByLabel("封面文件路径").fill("C:\\Videos\\custom-cover.png");
  await dialog.getByLabel("视频文件路径").fill("C:\\Videos\\video.mp4");
  await dialog.getByLabel("标题", { exact: true }).fill("我的视频");
  await dialog.getByRole("radio", { name: /自动投稿/ }).check();
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await dialog.getByRole("checkbox", { name: /我拥有该视频/ }).check();
  await dialog.getByRole("button", { name: "加入队列", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).createdTask)).toMatchObject({
    local_path: "C:\\Videos\\video.mp4", cover_path: "C:\\Videos\\custom-cover.png", mode: "auto",
  });
});

test("switching to YouTube does not submit the local cover", async ({ page }) => {
  await coverPickerBridge(page);
  const dialog = page.getByRole("dialog", { name: "新建投稿任务" });
  await dialog.getByRole("button", { name: "选择封面图片" }).click();
  await dialog.getByRole("button", { name: "YouTube 链接", exact: true }).click();
  await expect(dialog.getByLabel("封面文件路径")).toHaveCount(0);
  await dialog.getByLabel("YouTube 视频链接").fill("https://youtu.be/abcdefghijk");
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await dialog.getByRole("checkbox", { name: /我拥有该视频/ }).check();
  await dialog.getByRole("button", { name: "加入队列", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const params = await page.evaluate(() => (window as any).createdTask);
  expect(params).not.toHaveProperty("cover_path");
  expect(params).not.toHaveProperty("local_path");
});

test("material libraries create, filter, rename and confirm deletion", async ({
  page,
}) => {
  await nativePickerBridge(page);
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("combobox", { name: "素材库", exact: true }).click();
  await page.getByRole("option", { name: "示例素材库" }).click();
  await expect(page.locator(".material-card")).toHaveCount(3);
  await page.getByLabel("搜索素材").fill("参考");
  await expect(page.locator(".material-card")).toHaveCount(1);
  await page.getByRole("button", { name: "重命名素材" }).click();
  await page.getByLabel("文件名", { exact: true }).fill("新的参考图片.png");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".material-card strong")).toHaveText(
    "新的参考图片.png",
  );
  await page.getByRole("button", { name: "删除素材", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("任务记录会保留");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(1);
  await page.getByRole("button", { name: "删除素材", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(0);
  await page.getByLabel("搜索素材").fill("");
  await expect(page.locator(".material-card")).toHaveCount(2);
  await page.screenshot({
    path: "../output/materials-page.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建素材库", exact: true }).click();
  await expect(page.getByLabel("素材库名称", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "创建", exact: true })).toBeDisabled();
  await page.evaluate(() => { (window as any).fileSelection = "C:\\Materials\\旅行素材"; });
  await page.getByRole("button", { name: "选择文件夹", exact: true }).click();
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "素材库", exact: true }),
  ).toContainText("旅行素材");
  await expect(page.getByRole("heading", { name: "暂无素材" })).toBeVisible();
  await expect(page.locator(".material-path")).toHaveText("C:\\Materials\\旅行素材");
  expect(await page.evaluate(() => (window as any).createdLibrary)).toEqual({ directory: "C:\\Materials\\旅行素材" });
});

test("local materials picker filters media files, imports multiple files and handles cancellation", async ({ page }) => {
  await nativePickerBridge(page);
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("combobox", { name: "素材库", exact: true }).click();
  await page.getByRole("option", { name: "示例素材库" }).click();
  const add = page.getByRole("button", { name: "添加本地素材", exact: true });
  const buttons = page.locator(".material-toolbar button.secondary");
  await expect(buttons).toHaveText(["刷新", "添加本地素材", "打开文件夹"]);
  await page.evaluate(() => { (window as any).fileSelection = null; });
  await add.click();
  await expect(page.locator(".material-card")).toHaveCount(3);
  expect(await page.evaluate(() => (window as any).materialImportCalls ?? 0)).toBe(0);
  expect(await page.evaluate(() => (window as any).pickerOptions)).toMatchObject({
    multiple: true,
    filters: [{ extensions: ["mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "ts", "mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "weba", "jpg", "jpeg", "png", "webp", "gif", "avif", "bmp", "tiff"] }],
  });
  await page.getByLabel("搜索素材").fill("不会匹配");
  const paths = ["C:\\Files\\本地照片.png", "C:\\Files\\本地音频.mp3", "C:\\Files\\本地视频.mp4"];
  await page.evaluate(values => { (window as any).fileSelection = values; }, paths);
  await add.click();
  await expect(page.locator(".material-card")).toHaveCount(6);
  await expect(page.getByRole("status")).toContainText("本地素材导入完成。");
  expect(await page.evaluate(() => (window as any).importedMaterials)).toEqual({ library_id: "sample-library", paths });
  await page.getByRole("combobox", { name: "素材类型", exact: true }).click();
  await page.getByRole("option", { name: "音频", exact: true }).click();
  await expect(page.locator(".material-card")).toHaveCount(2);
  await expect(page.locator(".material-card strong")).toContainText(["背景音乐.mp3", "本地音频.mp3"]);
  await page.evaluate(() => { (window as any).materialImportError = "只支持图片、音频和视频文件。"; });
  await add.click();
  await expect(page.getByRole("alert")).toContainText("只支持图片、音频和视频文件。");
  await expect(page.locator(".material-card")).toHaveCount(2);
  await page.evaluate(() => { (window as any).materialImportError = "不支持的方法：materials.import"; });
  await add.click();
  await expect(page.getByRole("alert").filter({ hasText: "素材导入后台尚未更新" })).toContainText("请退出并重新启动应用后再试。");
  await expect(page.locator(".material-card")).toHaveCount(2);
});

test("download task library picker requires a location and preserves it when cancelled", async ({ page }) => {
  await nativePickerBridge(page);
  await page.goto("/?preview");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建下载素材任务" });
  await dialog.getByRole("button", { name: "新建素材库", exact: true }).click();
  await expect(dialog.getByLabel("素材库名称", { exact: true })).toHaveCount(0);
  const create = dialog.getByRole("button", { name: "创建", exact: true });
  const picker = dialog.getByRole("button", { name: "选择文件夹", exact: true });
  await page.evaluate(() => { (window as any).fileSelection = null; });
  await picker.click();
  await expect(create).toBeDisabled();
  await expect(dialog.getByLabel("素材库文件夹", { exact: true })).toHaveValue("");
  await page.evaluate(() => { (window as any).fileSelection = "/Volumes/素材硬盘/视频"; });
  await picker.click();
  expect(await page.evaluate(() => (window as any).pickerOptions)).toMatchObject({ directory: true, multiple: false });
  await expect(create).toBeEnabled();
  await page.evaluate(() => { (window as any).fileSelection = null; });
  await picker.click();
  await expect(dialog.getByLabel("素材库文件夹", { exact: true })).toHaveValue("/Volumes/素材硬盘/视频");
  await create.click();
  await expect(dialog.getByRole("combobox", { name: "素材库", exact: true })).toContainText("视频");
  expect(await page.evaluate(() => (window as any).createdLibrary)).toEqual({ directory: "/Volumes/素材硬盘/视频" });
  await dialog.getByRole("button", { name: "新建素材库", exact: true }).click();
  await expect(dialog.getByLabel("素材库文件夹", { exact: true })).toHaveValue("");
});

test("materials and submission center place New task in the same page heading position", async ({ page }) => {
  await page.goto("/?preview");
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 800 });
    await page.getByRole("button", { name: "投稿中心", exact: true }).click();
    const entry = page.locator(".page-heading").getByRole("button", { name: /新建(投稿|下载)任务/ });
    await expect(entry).toBeEnabled();
    await expect(entry).toContainText("新建投稿任务");
    const publishBounds = (await entry.boundingBox())!;
    await page.getByRole("button", { name: "素材管理", exact: true }).click();
    await expect(entry).toBeEnabled();
    await expect(entry).toContainText("新建下载任务");
    const downloadBounds = (await entry.boundingBox())!;
    expect(downloadBounds.x).toBeCloseTo(publishBounds.x, 0);
    expect(downloadBounds.y).toBeCloseTo(publishBounds.y, 0);
    expect(downloadBounds.width).toBeCloseTo(publishBounds.width, 0);
    expect(downloadBounds.height).toBeCloseTo(publishBounds.height, 0);
  }
});

test("materials only offers downloads and submission center only offers publishing", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=1");
  await expect(page.getByRole("heading", { name: "投稿中心", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  const dialog = page.getByRole("dialog", { name: /^新建(下载素材|投稿)任务$/ });
  await expect(dialog).toHaveAccessibleName("新建下载素材任务");
  await expect(dialog.getByRole("tablist", { name: "任务类型", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("tab", { name: "一键上传" })).toHaveCount(0);
  await expect(dialog.getByRole("tab", { name: "下载素材" })).toHaveCount(0);
  await expect(
    dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "加入下载队列" }),
  ).toBeDisabled();
  await dialog.getByLabel("素材链接").fill("https://example.com/video.mp4");
  await dialog.getByRole("combobox", { name: "素材库", exact: true }).click();
  await page.getByRole("option", { name: "示例素材库" }).click();
  await expect(
    dialog.getByRole("button", { name: "加入下载队列" }),
  ).toBeEnabled();
  await page.screenshot({ path: "../output/download-task.png" });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "投稿中心", exact: true }).click();
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  await expect(dialog.getByRole("tablist", { name: "任务类型", exact: true })).toHaveCount(0);
  await expect(dialog).toHaveAccessibleName("新建投稿任务");
  await expect(dialog.getByRole("tab", { name: "一键上传" })).toHaveCount(0);
  await expect(dialog.getByRole("tab", { name: "下载素材" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "加入下载队列" })).toHaveCount(0);
  await expect(dialog.getByRole("tab", { name: "图文上传" })).toBeVisible();
  await expect(dialog.getByLabel("YouTube 视频链接")).toBeVisible();
  await page.getByRole("button", { name: "本地视频", exact: true }).click();
  await expect(dialog.getByLabel("YouTube 视频链接")).toHaveCount(0);
  await dialog.getByLabel("视频文件路径").fill("C:\\Videos\\local.mp4");
  await expect(dialog.getByRole("button", { name: "选择封面图片" })).toBeVisible();
  await expect(dialog).toContainText("未选择时自动从视频抽帧");
  await dialog.getByLabel("封面文件路径").fill("C:\\Videos\\cover.png");
  await dialog.getByRole("button", { name: "清除封面", exact: true }).click();
  await expect(dialog.getByLabel("封面文件路径")).toHaveValue("");
  await dialog.getByLabel("封面文件路径").fill("C:\\Videos\\cover.png");
  await dialog.getByLabel("标题", { exact: true }).fill("自己填写的标题");
  await dialog.getByLabel("简介", { exact: true }).fill("自己填写的简介");
  await dialog.getByRole("combobox", { name: "目标 Bilibili 账号" }).click();
  await page.getByRole("option", { name: /UID 10001/ }).click();
  await dialog.getByRole("checkbox", { name: /我拥有该视频/ }).check();
  await expect(
    dialog.getByRole("button", { name: "加入队列", exact: true }),
  ).toBeEnabled();
  await expect(dialog).toContainText("投稿成功后保留源视频和任务工作目录");
  await dialog.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: "../output/local-publish-task.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const asset of [
  { type: "video", label: "视频", url: "https://example.com/video.mp4" },
  { type: "image", label: "图片", url: "https://example.com/photo.png" },
  { type: "audio", label: "音频", url: "https://example.com/audio.mp3" },
]) {
  test(`materials creates ${asset.type} downloads without a publishing account and allows repeat downloads`, async ({ page }) => {
    await nativePickerBridge(page);
    await page.goto("/?accounts=0");
    await page.getByRole("button", { name: "素材管理", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新建下载素材任务" });
    for (let index = 0; index < 2; index++) {
      await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
      await expect(dialog.getByRole("tab", { name: "一键上传" })).toHaveCount(0);
      await expect(dialog.getByRole("combobox", { name: "目标 Bilibili 账号" })).toHaveCount(0);
      const submit = dialog.getByRole("button", { name: "加入下载队列" });
      await expect(submit).toBeDisabled();
      await dialog.getByLabel("素材链接").fill(asset.url);
      await expect(submit).toBeDisabled();
      await dialog.getByRole("combobox", { name: "素材类型", exact: true }).click();
      await dialog.getByRole("option", { name: asset.label, exact: true }).click();
      await dialog.getByRole("combobox", { name: "素材库", exact: true }).click();
      await dialog.getByRole("option", { name: "示例素材库", exact: true }).click();
      await submit.click();
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "素材管理", exact: true })).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: "素材下载已加入队列。" })).toBeVisible();
    }
    const calls = await page.evaluate(() => (window as any).downloadCalls);
    expect(calls).toHaveLength(2);
    for (const params of calls) {
      expect(params).toEqual({
        url: asset.url, library_id: "sample-library", asset_type: asset.type,
        operation_id: expect.any(String),
      });
    }
    expect(calls[0].operation_id).not.toBe(calls[1].operation_id);
    expect(await page.evaluate(() => (window as any).createdTask)).toBeUndefined();
  });
}

test("download errors preserve the form and allow retrying the same operation", async ({ page }) => {
  await nativePickerBridge(page);
  await page.goto("/?accounts=0");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建下载素材任务" });
  await dialog.getByLabel("素材链接").fill("https://example.com/video.mp4");
  await dialog.getByRole("combobox", { name: "素材库", exact: true }).click();
  await dialog.getByRole("option", { name: "示例素材库", exact: true }).click();
  await page.evaluate(() => { (window as any).downloadError = "下载队列暂时不可用"; });
  await dialog.getByRole("button", { name: "加入下载队列" }).click();
  await expect(dialog.getByRole("alert")).toContainText("下载队列暂时不可用");
  await expect(dialog.getByLabel("素材链接")).toHaveValue("https://example.com/video.mp4");
  await expect(dialog.getByRole("combobox", { name: "素材库", exact: true })).toContainText("示例素材库");
  await page.evaluate(() => { (window as any).downloadError = ""; });
  await dialog.getByRole("button", { name: "加入下载队列" }).click();
  await expect(dialog).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).downloadCalls);
  expect(calls).toHaveLength(2);
  expect(calls[0]).toEqual(calls[1]);
});

test("library reads do not block opening, closing or reloading the download dialog", async ({ page }) => {
  await nativePickerBridge(page);
  await page.addInitScript(() => { (window as any).delayLibraryReads = true; });
  await page.goto("/?accounts=0");
  await page.getByRole("button", { name: "素材管理", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).pendingLibraryReads?.length ?? 0)).toBeGreaterThan(0);
  const initialReads = await page.evaluate(() => (window as any).pendingLibraryReads.length);
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建下载素材任务" });
  await expect.poll(() => page.evaluate(() => (window as any).pendingLibraryReads?.length ?? 0)).toBeGreaterThan(initialReads);
  await expect(dialog.getByRole("button", { name: "取消", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.evaluate(() => {
    const w = window as any;
    w.delayLibraryReads = false;
    for (const resolve of w.pendingLibraryReads) resolve();
  });
  await page.getByRole("button", { name: /新建(投稿|下载)任务/ }).click();
  await dialog.getByRole("combobox", { name: "素材库", exact: true }).click();
  await dialog.getByRole("option", { name: "示例素材库", exact: true }).click();
  await expect(dialog.getByRole("combobox", { name: "素材库", exact: true })).toContainText("示例素材库");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("new task shortcuts follow the current page and reopening uses the correct form", async ({ page }) => {
  await page.goto("/?preview");
  const dialog = page.getByRole("dialog", { name: /^新建(下载素材|投稿)任务$/ });
  for (const shortcut of ["Control+n", "Meta+n"]) {
    await page.getByRole("button", { name: "素材管理", exact: true }).click();
    await page.keyboard.press(shortcut);
    await expect(dialog).toHaveAccessibleName("新建下载素材任务");
    await expect(dialog.getByLabel("素材链接")).toBeVisible();
    await expect(dialog.getByRole("tab", { name: "一键上传" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await page.getByRole("button", { name: "投稿中心", exact: true }).click();
    await page.keyboard.press(shortcut);
    await expect(dialog).toHaveAccessibleName("新建投稿任务");
    await expect(dialog.getByRole("tab", { name: "一键上传" })).toHaveCount(0);
    await expect(dialog.getByLabel("YouTube 视频链接")).toBeVisible();
    await expect(dialog.getByRole("tab", { name: "下载素材" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "关闭对话框" }).click();
    await expect(dialog).toHaveCount(0);
  }
});

for (const language of [
  { choice: "English", nav: "Materials", title: "New material download task", entry: "New download task", publish: "New submission task" },
  { choice: "繁體中文", nav: "素材管理", title: "新建下載素材任務", entry: "新建下載任務", publish: "新建投稿任務" },
]) {
  test(`material interface localizes to ${language.choice}`, async ({
    page,
  }) => {
    await page.goto("/?preview");
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("combobox", { name: "界面语言" }).click();
    await page.getByRole("option", { name: language.choice }).click();
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect(
      page.getByRole("button", { name: language.nav, exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: language.nav, exact: true }).click();
    await page.getByRole("button", { name: new RegExp(`^${language.entry}`) }).click();
    const dialog = page.getByRole("dialog", { name: language.title, exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("tab")).toHaveCount(0);
    await dialog.locator(".modal-head button").click();
    await page.locator("nav .nav-item").first().click();
    await page.getByRole("button", { name: new RegExp(`^${language.publish}`) }).click();
    await expect(page.getByRole("dialog", { name: language.publish, exact: true })).toBeVisible();
  });
}
