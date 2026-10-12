import { test, expect } from "@playwright/test";

const platforms = [
  ["Bilibili", "bilibili"], ["AcFun", "acfun"], ["抖音", "douyin"], ["YouTube", "youtube"],
  ["X（Twitter）", "twitter"], ["Facebook Page", "facebook"], ["TikTok", "tiktok"], ["小红书", "xiaohongshu"],
];

test("task center filters every supported platform with status and search", async ({ page }) => {
  await page.goto("/?preview&populated&accounts=2&taskFilters");
  await expect(page.getByRole("combobox", { name: "按账号筛选" })).toHaveCount(0);
  const rows = page.locator("button.task-row");
  await expect(rows).toHaveCount(20);
  const status = page.getByRole("combobox", { name: "按任务状态筛选" });
  for (const [name, platform] of platforms) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(rows).toHaveCount(3);
    for (const [index, label] of ["待预览", "下载完成", "失败"].entries()) {
      await status.click();
      await page.getByRole("option", { name: label, exact: true }).click();
      await expect(rows).toHaveCount(1);
      await expect(rows).toContainText(`${platform}-${index}`);
    }
    await status.click();
    await page.getByRole("option", { name: "全部状态", exact: true }).click();
  }
  await page.getByRole("textbox", { name: "搜索任务" }).fill("xiaohongshu-1");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("xiaohongshu 任务 2");
  await page.getByRole("textbox", { name: "搜索任务" }).fill("");
  await page.getByRole("tab", { name: /全部任务/ }).click();
  await expect(rows).toHaveCount(20);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(rows).toHaveCount(4);
  await page.getByRole("tab", { name: "Bilibili", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.getByRole("button", { name: "投稿记录", exact: true }).click();
  await page.getByRole("tab", { name: "AcFun", exact: true }).click();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: /^任务中心/ }).click();
  await expect(page.getByRole("tab", { name: "Bilibili", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(rows).toHaveCount(3);
  for (const width of [1280, 1024, 600]) {
    await page.setViewportSize({ width, height: 800 });
    await page.locator(".task-panel").scrollIntoViewIfNeeded();
    const positions = await page.locator(".tasks-toolbar").evaluate((toolbar) => ({
      tabs: toolbar.querySelector(".tabs")!.getBoundingClientRect().toJSON(),
      search: toolbar.querySelector(".search")!.getBoundingClientRect().toJSON(),
      status: toolbar.querySelector('[role="combobox"]')!.getBoundingClientRect().toJSON(),
      toolbar: toolbar.getBoundingClientRect().toJSON(),
      table: document.querySelector(".table-head")!.getBoundingClientRect().toJSON(),
    }));
    expect(Math.abs(positions.search.top + positions.search.height / 2 - positions.status.top - positions.status.height / 2)).toBeLessThan(2);
    expect(Math.abs(positions.tabs.top + positions.tabs.height / 2 - positions.status.top - positions.status.height / 2)).toBeLessThan(2);
    expect(positions.tabs.right).toBeLessThan(positions.search.left);
    expect(positions.toolbar.bottom).toBeLessThanOrEqual(positions.table.top);
    expect(positions.toolbar.right).toBeLessThanOrEqual(width);
    await page.getByRole("tab", { name: "小红书", exact: true }).click();
    await expect(rows).toHaveCount(3);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({ path: "/private/tmp/screator-task-filters.png", fullPage: true });
});
