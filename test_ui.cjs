// npm install --prefix .qa playwright, then node server/test_ui.cjs (from repository root).
const { chromium } = require("../.qa/node_modules/playwright");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "..");
const pages = JSON.parse(
  execFileSync("python", [path.join(__dirname, "ui_fixtures.py")], {
    encoding: "utf8",
  }),
);
const sizes = [
  [1280, 800],
  [1024, 768],
  [768, 1024],
  [800, 600],
  [600, 960],
  [390, 844],
  [667, 375],
  [320, 568],
];
let checks = 0;
async function fits(page, label) {
  const bad = await page.evaluate(() =>
    [
      ...document.querySelectorAll(
        "dialog[open],dialog[open] button,header,main section:not([hidden]),main section:not([hidden]) button,main section:not([hidden]) input:not([type=hidden]),main section:not([hidden]) select,main section:not([hidden]) a",
      ),
    ]
      .filter(
        (e) => e.getClientRects().length && !e.closest("dialog:not([open])"),
      )
      .map((e) => ({
        tag: e.tagName,
        text: e.textContent.slice(0, 50),
        r: e.getBoundingClientRect().toJSON(),
      }))
      .filter(
        (e) =>
          e.r.x < -1 ||
          e.r.y < -1 ||
          e.r.right > innerWidth + 1 ||
          e.r.bottom > innerHeight + 1,
      ),
  );
  if (bad.length) throw Error(label + " " + JSON.stringify(bad));
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollHeight > innerHeight + 1 ||
      document.documentElement.scrollWidth > innerWidth + 1,
  );
  if (overflow) throw Error(label + " document overflow");
  checks++;
}
(async () => {
  const browser = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
  });
  try {
    for (const [width, height] of sizes) {
      for (const [name, html] of Object.entries(pages)) {
        const page = await browser.newPage({ viewport: { width, height } });
        let authenticated = false;
        const folder = name.startsWith("kpp") ? "server" : name;
        await page.route("https://app.test/**", async (route) => {
          const u = new URL(route.request().url());
          let body = {},
            type = "application/json";
          if (u.pathname === "/") {
            body = html;
            type = "text/html";
          } else if (u.pathname.startsWith("/static/")) {
            body = fs.readFileSync(path.join(root, folder, u.pathname));
            type = u.pathname.endsWith(".js") ? "text/javascript" : "text/css";
          } else if (u.pathname === "/status")
            body = {
              online: true,
              user: authenticated ? { id: 1, username: "operator" } : null,
            };
          else if (u.pathname === "/login") {
            authenticated = true;
            body = { user: { id: 1, username: "operator" } };
          } else if (u.pathname.includes("/stats/today"))
            body = {
              current_price: 30000,
              entry_price: 30000,
              reload_price: 50000,
              entry_enabled: true,
              reload_enabled: true,
              count: 123,
              total_fmt: "3 690 000",
            };
          else if (u.pathname === "/bridge/api/weighings")
            body = {
              weighing: {
                id: 1,
                plate_number: "01 A 123 BA",
                status: "pending",
                weighing_fee: 30000,
              },
              service_prices: {
                entry: 30000,
                reload: 50000,
                entry_enabled: true,
                reload_enabled: true,
              },
            };
          else if (u.pathname.endsWith("/quote"))
            body = {
              ticket: "test",
              quote: {
                plate: "01 A 123 BA",
                days: 12,
                rate: 30000,
                paid: 30000,
                amount: 330000,
              },
            };
          else if (
            u.pathname.includes("operator/weighings") ||
            u.pathname.endsWith("/recent")
          ) {
            const rows = Array.from({ length: 20 }, (_, id) => ({
              id,
              plate: "01 A 123 BA",
              plate_number: "01 A 123 BA",
              price_fmt: "110 000",
              amount: 30000,
              status: "paid",
            }));
            body = { rows, results: rows, total: 20, total_pages: 1 };
          } else if (u.pathname.endsWith("/pay"))
            body = {
              receipt: { plate_number: "01 A 123 BA", total_fmt: "110 000" },
            };
          else if (u.pathname.endsWith("/commit")) body = { id: 1 };
          else if (u.pathname.startsWith("/print/"))
            body = {
              message:
                "Windows printer navbatiga yuborildi. Qog‘ozni tekshiring.",
            };
          await route.fulfill({
            status: 200,
            contentType: type,
            body:
              typeof body === "string" || Buffer.isBuffer(body)
                ? body
                : JSON.stringify(body),
          });
        });
        await page.goto("https://app.test/");
        await fits(page, `${name} ${width}x${height}`);
        if (!name.startsWith("kpp")) {
          await page.locator("[name=username]").fill("operator");
          await page.locator("[name=password]").fill("test");
          await page.locator("#login-form button").click();
          await page.locator("#home").waitFor({ state: "visible" });
          await fits(page, `${name} home ${width}x${height}`);
          await page.locator("[data-action]").first().click();
          await fits(page, `${name} plate ${width}x${height}`);
          await page.locator("#plate-number").fill("01 A 123 BA");
          await page.locator("#plate-form button.primary").click();
          await page.locator("#payment").waitFor({ state: "visible" });
          await fits(page, `${name} payment ${width}x${height}`);
          if (name === "tarozi") {
            await page.locator("#weight").fill("18000");
            await page.locator("#entry").check();
            await page.locator("#reload").check();
          }
          await page.locator("#received").check();
          await page.locator("#pay").click();
          await page.locator("#result").waitFor({ state: "visible" });
          await fits(page, `${name} result ${width}x${height}`);
          await page.locator("#result [data-home]").click();
          await page.locator("#history-open").click();
          await page.locator("#history").waitFor({ state: "visible" });
          await fits(page, `${name} history ${width}x${height}`);
        } else if (name === "kpp-station") {
          await page.locator("#journal-open").click();
          await fits(page, `kpp journal ${width}x${height}`);
          await page.locator("#journal-close").click();
          await page.locator("[data-action]").first().click();
          await fits(page, `kpp vehicle ${width}x${height}`);
        }
        await page.close();
      }
    }
    console.log(
      `PASS: ${checks} viewport/state checks across ${sizes.length} screen sizes`,
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
