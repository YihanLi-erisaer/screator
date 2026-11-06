import { test, expect } from "@playwright/test";
import fs from "node:fs";
const hk = JSON.parse(
  fs.readFileSync(new URL("../src/messagesHk.json", import.meta.url), "utf8"),
);

test("membership preview is explicitly unconfigured and preserves local publishing", async ({
  page,
}) => {
  await page.goto("/?preview&populated&accounts=1");
  await expect(page.locator(".task-row")).toHaveCount(3);
  await page.getByRole("button", { name: "会员订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会员订阅", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "会员服务尚未配置", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("界面预览不会创建真实会员账号或模拟支付成功。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /确认购买/ })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "会员邮箱" })).toHaveCount(0);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: /^投稿中心/ })
    .click();
  await expect(page.locator(".task-row")).toHaveCount(3);
});

for (const [locale, title, heading] of [
  ["en", "Membership", "Membership service is not configured"],
  ["zh-HK", hk["会员订阅"], hk["会员服务尚未配置"]],
] as const) {
  test(`${locale}: membership safety guidance is localized`, async ({
    page,
  }) => {
    await page.goto(`/?preview&uiLanguage=${locale}`);
    await page.getByRole("button", { name: title, exact: true }).click();
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    expect(
      await page
        .locator(".membership-page")
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true);
    const padding = await page
      .locator(".membership-account")
      .evaluate((element) =>
        Number.parseInt(getComputedStyle(element).paddingLeft),
      );
    expect(padding).toBeGreaterThanOrEqual(20);
  });
}
