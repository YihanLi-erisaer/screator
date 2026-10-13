import { expect, test } from "@playwright/test";
import fs from "node:fs";
const en = JSON.parse(
  fs.readFileSync(new URL("../src/messagesEn.json", import.meta.url), "utf8"),
);
const hk = JSON.parse(
  fs.readFileSync(new URL("../src/messagesHk.json", import.meta.url), "utf8"),
);

const english = en as Record<string, string>;
const traditional = hk as Record<string, string>;

for (const locale of ["en", "zh-HK"] as const) {
  test(`${locale}: every navigation page and lazy panel is localized`, async ({
    page,
  }) => {
    await page.goto(
      `/?preview&populated&accounts=1&acfun=1&youtube=1&xiaohongshu=1&uiLanguage=${locale}`,
    );
    const dictionary = locale === "en" ? english : traditional;
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    for (const label of [
      "投稿中心",
      "素材管理",
      "投稿记录",
      "数据中心",
      "账号与连接",
      "设置",
    ]) {
      await page
        .locator("nav button")
        .filter({ hasText: dictionary[label] })
        .click();
      await expect(page.locator("h1")).toHaveText(dictionary[label]);
    }
    await expect(
      page.getByRole("combobox", { name: dictionary["界面语言"] }),
    ).toContainText(locale === "en" ? "English" : "繁體中文");
    await expect(
      page.getByText(dictionary["每 2 秒刷新；更新时间"]),
    ).toBeVisible();
    await expect(
      page.getByText(dictionary["开发预览 · 尚未安装本地组件"]),
    ).toBeVisible();
    await page
      .getByRole("combobox", {
        name: dictionary["AcFun 默认分区（自动投稿必填）"],
      })
      .click();
    await expect(
      page.getByRole("option", {
        name:
          locale === "en"
            ? /Technology \/ Technology and making/
            : /科技 \/ 科技製造/,
      }),
    ).toBeVisible();
  });
}

test("English UI has no untranslated application copy across all pages", async ({
  page,
}) => {
  await page.goto(
    "/?preview&populated&accounts=1&acfun=1&youtube=1&xiaohongshu=1&uiLanguage=en",
  );
  for (const label of [
    "投稿中心",
    "素材管理",
    "投稿记录",
    "数据中心",
    "账号与连接",
    "设置",
  ]) {
    await page
      .locator("nav button")
      .filter({ hasText: english[label] })
      .click();
    await expect(page.locator("h1")).toHaveText(english[label]);
    await expect
      .poll(async () =>
        page.evaluate(() => {
          const result: string[] = [];
          const walker = document.createTreeWalker(
            document.querySelector("main")!,
            NodeFilter.SHOW_TEXT,
          );
          let node: Node | null;
          while ((node = walker.nextNode())) {
            const parent = node.parentElement;
            const value = node.textContent?.trim() || "";
            if (
              !parent ||
              parent.closest("[data-no-localize],script,style") ||
              !/[\u3400-\u9fff]/u.test(value)
            )
              continue;
            if (!parent.getClientRects().length) continue;
            // Development fixtures represent user-authored account names and titles.
            if (
              /^(账号 \d+( · UID \d+)?|AcFun 预览账号|YouTube 预览频道|小红书预览账号|用更少的工具|关于设计系统|让创作回归简单)/.test(
                value,
              )
            )
              continue;
            if (
              /^ · (AcFun 预览账号|YouTube 预览频道|小红书预览账号)/.test(value)
            )
              continue;
            result.push(value);
          }
          return result;
        }),
      )
      .toEqual([]);
  }
});

test("saving language preferences updates mounted UI and supports round trips", async ({
  page,
}) => {
  await page.goto("/?preview&accounts=1");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("combobox", { name: "界面语言" }).click();
  await page.getByRole("option", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(
    page.getByRole("status").filter({ hasText: english["设置已保存。"] }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: english["界面语言"] }).click();
  await page.getByRole("option", { name: "繁體中文", exact: true }).click();
  await page
    .getByRole("button", { name: english["保存设置"], exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-HK");
  await page.getByRole("combobox", { name: traditional["界面语言"] }).click();
  await page.getByRole("option", { name: "简体中文", exact: true }).click();
  await page
    .getByRole("button", { name: traditional["保存设置"], exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(
    page.getByRole("button", { name: "保存设置", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".success-toast")).toContainText("设置已保存。");
});

test("native confirmations localize the complete sentence and preserve account names", async ({
  page,
}) => {
  await page.goto("/?preview&accounts=1&uiLanguage=en");
  await page
    .getByRole("button", { name: english["账号与连接"], exact: true })
    .click();
  const confirmation = new Promise<string>((resolve) =>
    page.once("dialog", async (dialog) => {
      resolve(dialog.message());
      await dialog.dismiss();
    }),
  );
  await page
    .locator(".account-row")
    .getByRole("button", { name: english["退出登录"], exact: true })
    .click();
  expect(await confirmation).toBe(
    "Clear login credentials for 账号 1 · UID 10001? Tasks and the account will be retained.",
  );
});

test("language changes preserve active modal edits and React updates", async ({
  page,
}) => {
  await page.goto("/?preview&populated&accounts=1&uiLanguage=en");
  await page.getByRole("button", { name: /用更少的工具/ }).click();
  const dialog = page.getByRole("dialog");
  const title = dialog.getByLabel(new RegExp(english["中文标题"]));
  await title.fill("设置");
  await page.evaluate(async () => {
    const i18n = await import("/src/i18n.ts" /* @vite-ignore */);
    i18n.setUiLanguage("zh-HK");
  });
  await expect(dialog.getByLabel(/中文標題/)).toHaveValue("设置");
  await page.evaluate(async () => {
    const i18n = await import("/src/i18n.ts" /* @vite-ignore */);
    i18n.setUiLanguage("en");
  });
  await expect(title).toHaveValue("设置");
  await dialog
    .getByRole("button", { name: english["保存修改"], exact: true })
    .click();
  await expect(dialog.locator("h3").first()).toHaveText("设置");
  await dialog.getByRole("button", { name: english["关闭对话框"] }).click();
  await expect(
    page.locator("button.task-row").filter({ hasText: "设置" }),
  ).toBeVisible();
});

test("backend messages and templates preserve parameters including whitespace", async ({
  page,
}) => {
  await page.goto("/?preview&uiLanguage=en");
  const values = await page.evaluate(async () => {
    const i18n = await import("/src/i18n.ts" /* @vite-ignore */);
    i18n.setUiLanguage("en");
    return [
      i18n.uiText("已导出 12 条投稿记录。"),
      i18n.uiText("请选择有效的 Bilibili 账号。"),
      i18n.uiText("Error: 登录失效，请重新登录该账号。"),
      i18n.uiText("翻译组件需要预留约 7.5 GiB 可用空间。"),
      i18n.uiText("未连接到后台：{p0}", { p0: "原始错误\n  /用户/路径" }),
      i18n.uiText("后台启动失败：原始错误\n  /用户/路径"),
      i18n.uiText("未收录的外部平台原始回复"),
    ];
  });
  expect(values).toEqual([
    "Exported 12 publication records.",
    "Select a valid Bilibili account.",
    "Error: Login expired. Log in to this account again.",
    "Translation components require approximately 7.5 GiB of free space.",
    "Backend disconnected: 原始错误\n  /用户/路径",
    "Backend startup failed: 原始错误\n  /用户/路径",
    "未收录的外部平台原始回复",
  ]);
});

test("data center translates review status while preserving publication titles", async ({
  page,
}) => {
  await page.goto("/?preview&populated&accounts=1&uiLanguage=en");
  await page
    .getByRole("button", { name: english["数据中心"], exact: true })
    .click();
  await expect(page.locator(".data-row")).toContainText("Approved");
  await expect(page.locator(".data-row")).toContainText("让创作回归简单");
});

test("English layout stays within a compact desktop window", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 680 });
  await page.goto(
    "/?preview&populated&accounts=5&acfun=1&youtube=1&uiLanguage=en",
  );
  for (const label of [
    "投稿中心",
    "素材管理",
    "投稿记录",
    "数据中心",
    "账号与连接",
    "设置",
  ]) {
    await page
      .locator("nav button")
      .filter({ hasText: english[label] })
      .click();
    await expect(page.locator("h1")).toHaveText(english[label]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});
