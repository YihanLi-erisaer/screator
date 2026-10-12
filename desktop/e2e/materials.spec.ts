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
  await page.getByRole("button", { name: /新建任务/ }).click();
  await page.getByRole("tab", { name: /^(同步投稿|一键上传)$/ }).click();
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
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
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
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
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
  const dialog = page.getByRole("dialog", { name: "新建任务" });
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
  const dialog = page.getByRole("dialog", { name: "新建任务" });
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
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
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

test("new task defaults to downloads without account and local publishing exposes metadata", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: /新建任务/ }).click();
  const dialog = page.getByRole("dialog", { name: "新建任务" });
  await expect(page.getByRole("tab", { name: "下载素材" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
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
  await page.getByRole("tab", { name: /^(同步投稿|一键上传)$/ }).click();
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
  await page.screenshot({ path: "../output/local-publish-task.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const language of [
  { choice: "English", nav: "Materials", tab: "Download materials" },
  { choice: "繁體中文", nav: "素材管理", tab: "下載素材" },
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
    await page.locator("nav .nav-item").first().click();
    await page.locator(".page-heading button.primary").click();
    await expect(page.getByRole("tab", { name: language.tab })).toBeVisible();
  });
}
