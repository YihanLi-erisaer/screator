import { test, expect } from "@playwright/test";

test("history combines platform, status and video ID filters", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=2&historyFilters");
  // The task-center platform must not silently restrict history.
  await page.getByRole("tab", { name: "AcFun", exact: true }).click();
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "按账号筛选" })).toHaveCount(0);
  const rows = page.locator("button.task-row");
  await expect(rows).toHaveCount(20);
  const status = page.getByRole("combobox", { name: "按投稿状态筛选" });
  for (const [name, platform] of [
    ["Bilibili", "bilibili"], ["AcFun", "acfun"], ["抖音", "douyin"], ["YouTube", "youtube"],
    ["X（Twitter）", "twitter"], ["Facebook Page", "facebook"], ["TikTok", "tiktok"], ["小红书", "xiaohongshu"],
  ]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(rows).toHaveCount(4);
    for (const [index, label] of ["已提交", "待核对", "部分已提交", "已结束"].entries()) {
      await status.click();
      await page.getByRole("option", { name: label, exact: true }).click();
      await expect(rows).toHaveCount(1);
      await expect(rows).toContainText(`${platform}-${index}`);
    }
    await status.click();
    await page.getByRole("option", { name: "全部状态", exact: true }).click();
  }
  await page.getByRole("tab", { name: "YouTube", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索任务" }).fill("youtube-1");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("youtube 投稿 2");
  await page.getByRole("textbox", { name: "搜索任务" }).fill("");
  await expect(rows).toHaveCount(4);
  for (const width of [1280, 1024, 600]) {
    await page.setViewportSize({ width, height: 800 });
    await page.locator(".task-panel").scrollIntoViewIfNeeded();
    const { tabsBox, searchBox, statusBox } = await page.locator(".history-toolbar").evaluate((toolbar) => ({
      tabsBox: toolbar.querySelector(".tabs")!.getBoundingClientRect().toJSON(),
      searchBox: toolbar.querySelector(".search")!.getBoundingClientRect().toJSON(),
      statusBox: toolbar.querySelector('[role="combobox"]')!.getBoundingClientRect().toJSON(),
    }));
    expect(Math.abs(searchBox.y + searchBox.height / 2 - statusBox.y - statusBox.height / 2)).toBeLessThan(2);
    expect(Math.abs(tabsBox.y + tabsBox.height / 2 - statusBox.y - statusBox.height / 2)).toBeLessThan(2);
    expect(tabsBox.right).toBeLessThan(searchBox.left);
    expect(searchBox.x + searchBox.width).toBeLessThan(statusBox.x);
    expect(statusBox.right).toBeLessThanOrEqual(width);
    await page.getByRole("tab", { name: "小红书", exact: true }).click();
    await expect(rows).toHaveCount(4);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({ path: "/private/tmp/screator-history-filters.png", fullPage: true });
});

test("history filters imported platform records", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=1&importedHistory");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("tab", { name: "AcFun", exact: true }).click();
  await page.getByRole("combobox", { name: "按投稿状态筛选" }).click();
  await page.getByRole("option", { name: "待核对", exact: true }).click();
  await expect(page.locator("button.task-row")).toHaveCount(1);
  await expect(page.locator("button.task-row")).toContainText("迁移的投稿记录");
  await page.getByRole("tab", { name: "Bilibili", exact: true }).click();
  await expect(page.locator("button.task-row")).toHaveCount(0);
});

test("changing history filters resets pagination", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=1&manyTasks&importedHistory");
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByRole("tab", { name: "AcFun", exact: true }).click();
  await expect(page.locator("button.task-row")).toHaveCount(1);
  await expect(page.locator("button.task-row")).toContainText("迁移的投稿记录");
  await page.getByRole("tab", { name: /全部记录/ }).click();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByRole("combobox", { name: "按投稿状态筛选" }).click();
  await page.getByRole("option", { name: "待核对", exact: true }).click();
  await expect(page.locator("button.task-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
});
