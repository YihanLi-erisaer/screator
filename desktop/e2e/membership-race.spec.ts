import { test, expect } from "@playwright/test";

// Deterministic IPC timing fixture, not payment acceptance. The cross-repository
// browser suite separately exercises actual HTTP, signed callbacks and grants.
test("a terminal order arriving after stale status refreshes membership again", async ({
  page,
}) => {
  test.setTimeout(20000);
  await page.addInitScript(() => {
    const fixture = {
      state: "pending",
      reads: 0,
      refundOnNextOrder: false,
      statusReads: [] as string[],
      async invoke(method: string) {
        const order = () => ({
          id: "89122ce6-48e0-47f6-aa49-94f9c8b82a3d",
          plan_id: "standard_monthly",
          tier: "standard",
          months: 1,
          amount: 1900,
          currency: "CNY",
          provider: "stripe",
          state: fixture.state,
          payment_type: "redirect",
          created_at: new Date().toISOString(),
          expires_at: new Date(Date.now() + 3600000).toISOString(),
          has_payment_link: false,
          qr_image: null,
        });
        if (method === "membership.status") {
          fixture.statusReads.push(fixture.state);
          return {
            configured: true,
            service_url: "https://fixture.example.invalid",
            authenticated: true,
            user: { id: "fixture-member", email: "timing@example.invalid" },
            subscription: {
              tier: fixture.state === "paid" ? "standard" : "free",
              expires_at: null,
              features: [],
            },
          };
        }
        if (method === "membership.plans" || method === "membership.providers")
          return [];
        if (method === "membership.orders")
          return {
            count: 1,
            results: [order()],
            pending_checkouts: [],
            pending_error: "",
          };
        if (method === "membership.order") {
          fixture.reads += 1;
          // The first read selects the order. The second follows an already-read
          // free membership snapshot, exactly when a payment could commit.
          if (fixture.refundOnNextOrder) {
            fixture.state = "revoked";
            fixture.refundOnNextOrder = false;
          } else if (fixture.reads === 2) fixture.state = "paid";
          return order();
        }
        throw new Error("Unexpected membership RPC in timing fixture");
      },
    };
    (window as any).__membershipRace = fixture;
  });
  await page.route("**/src/preview.ts", async (route) => {
    const response = await route.fetch();
    const source = await response.text();
    const opening = /async function request\(method, params\) \{/;
    expect(source).toMatch(opening);
    const body = source.replace(
      opening,
      (match) =>
        `${match}\nif (method.startsWith("membership.")) return window.__membershipRace.invoke(method, params);`,
    );
    await route.fulfill({ response, body });
  });
  await page.goto("/?preview");
  await page.getByRole("button", { name: "会员订阅", exact: true }).click();
  await expect(page.locator(".membership-tier")).toHaveText("未订阅");
  await page.locator(".membership-order-row").first().click();
  await expect(page.locator(".membership-checkout")).toContainText("已支付");
  await expect(page.locator(".membership-tier")).toHaveText("Standard", {
    timeout: 5000,
  });
  expect(
    await page.evaluate(() =>
      (window as any).__membershipRace.statusReads.slice(-2),
    ),
  ).toEqual(["pending", "paid"]);

  await page.evaluate(() => {
    (window as any).__membershipRace.refundOnNextOrder = true;
  });
  await page.getByRole("button", { name: "刷新会员状态", exact: true }).click();
  await expect(page.locator(".membership-checkout")).toContainText(
    "权益已撤销",
  );
  await expect(page.locator(".membership-tier")).toHaveText("未订阅", {
    timeout: 5000,
  });
  expect(
    await page.evaluate(() =>
      (window as any).__membershipRace.statusReads.slice(-2),
    ),
  ).toEqual(["paid", "revoked"]);
});
