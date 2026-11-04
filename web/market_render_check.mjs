/** Acceptance against an isolated running app with real stored market data. */
import { chromium } from "playwright-core";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const base = process.env.OQ_BASE || "http://127.0.0.1:8017";
const password = process.env.OQ_PASSWORD_FILE
  ? readFileSync(process.env.OQ_PASSWORD_FILE, "utf8").trim()
  : process.env.OQ_PASSWORD;
assert(password, "Set OQ_PASSWORD_FILE or OQ_PASSWORD for the isolated test account");
const output = resolve(process.env.OQ_SCREENSHOTS || "../_vps/ui-preview/screenshots");
mkdirSync(output, { recursive: true });
const executablePath = process.env.OQ_CHROME || [
  "D:/Chrome-bin/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find(existsSync);
const browser = await chromium.launch({ executablePath, headless: true });
const context = await browser.newContext({ viewport: { width: 1536, height: 1050 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", e => errors.push(e.message));
page.on("console", msg => { if (msg.type() === "error" && !msg.text().includes("503")) errors.push(msg.text()); });
try {
  await page.goto(base + "/login");
  await page.locator("#u").fill("operator");
  await page.locator("#p").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL(base + "/app");
  await page.getByRole("img", { name: /BTC-USDT-SWAP.*收盘 K 线/ }).locator("canvas").waitFor();
  await page.screenshot({ path: resolve(output, "overview-desktop.png"), fullPage: true });
  assert(await page.locator("canvas").count() >= 3, "Overview should render market, equity and risk charts");
  await page.getByRole("link", { name: "行情终端" }).click();
  await page.waitForURL(base + "/app/markets");
  await page.getByRole("heading", { name: "行情终端", exact: true }).waitFor();
  const chart = page.getByRole("img", { name: /BTC-USDT-SWAP.*收盘 K 线/ });
  await chart.locator("canvas").waitFor();
  await page.screenshot({ path: resolve(output, "markets-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: /ETH 永续/ }).click();
  await page.getByRole("img", { name: /ETH-USDT-SWAP 15m/ }).locator("canvas").waitFor();
  await page.getByRole("button", { name: "1 小时", exact: true }).click();
  await page.getByRole("img", { name: /ETH-USDT-SWAP 1h/ }).locator("canvas").waitFor();
  await page.getByRole("checkbox", { name: "均线", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "均线", exact: true }).check();
  await page.getByRole("textbox", { name: "搜索币种" }).fill("NOT-A-COIN");
  await page.getByText("没有匹配的币种", { exact: true }).waitFor();
  await page.getByRole("textbox", { name: "搜索币种" }).fill("");

  // Force out-of-order real responses; a slow prior symbol must never win.
  await page.route("**/api/v1/app/market/candles?**", async route => {
    const response = await route.fetch();
    if (route.request().url().includes("symbol=SOL")) await new Promise(r => setTimeout(r, 800));
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: /SOL 永续/ }).click();
  await page.getByRole("button", { name: /BTC 永续/ }).click();
  await page.getByRole("img", { name: /BTC-USDT-SWAP 1h/ }).locator("canvas").waitFor();
  await page.waitForTimeout(1000);
  assert(await page.getByRole("img", { name: /BTC-USDT-SWAP 1h/ }).count() === 1);
  await page.unroute("**/api/v1/app/market/candles?**");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector("aside")?.getBoundingClientRect().right <= 1);
  await page.screenshot({ path: resolve(output, "markets-mobile.png"), fullPage: true, animations: "disabled" });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile must not overflow horizontally");
  const size = await page.locator("canvas").first().boundingBox();
  assert(size && size.width > 200 && size.height > 200, "Chart should resize for mobile");

  await page.route("**/api/v1/app/market/candles?**", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "验收：行情暂不可用" }) }));
  await page.getByRole("button", { name: "4 小时", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "验收：行情暂不可用" }).waitFor();
  assert(await page.getByRole("img", { name: /收盘 K 线/ }).count() === 0, "Failed new period must not retain another period's chart");
  await page.unroute("**/api/v1/app/market/candles?**");
  await page.getByRole("button", { name: /刷新行情/ }).click();
  await page.getByRole("img", { name: /BTC-USDT-SWAP 4h/ }).locator("canvas").waitFor();
  await page.setViewportSize({ width: 1536, height: 1050 });
  await page.goto(base + "/app/decisions");
  await page.getByRole("img", { name: "数据图表" }).locator("canvas").waitFor();
  await page.getByText(/cost_edge · edge/).first().waitFor();
  assert.deepEqual(errors, [], "No runtime or unexpected console errors");
  console.log("PASS: desktop/mobile charts, real API login, symbol/timeframe/MA/search, racing responses, failure recovery, rejection reasons");
  console.log("Screenshots:", output);
} finally { await browser.close(); }
