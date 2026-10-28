/**
 * Renders every route in a real browser and reports what actually painted.
 *
 * Screenshots alone are not evidence we can read, so this asserts on rendered
 * text, console errors and failed requests instead — the three things that
 * break a SPA without changing an HTTP status code.
 */
import { chromium } from "playwright-core";

const CHROME =
  process.env.OQ_CHROME ||
  process.env.CHROME_PATH ||
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = process.env.OQ_BASE || "https://www.kairosigma.eu.cc";
const PASSWORD = process.env.OQ_PASSWORD || "";

const PUBLIC_ROUTES = [
  ["/", ["Origin Quant", "一个操作员"]],
  ["/docs", ["使用文档", "快速开始"]],
];

const APP_ROUTES = [
  ["/app", ["总览", "净值"]],
  ["/app/positions", ["持仓", "套利账本"]],
  ["/app/orders", ["订单 / 成交"]],
  ["/app/decisions", ["决策流水"]],
  ["/app/backtest", ["回测", "开始回测"]],
  ["/app/data", ["数据新鲜度"]],
];

const ADMIN_ROUTES = [
  ["/admin", ["系统概览", "表记录数"]],
  ["/admin/users", ["用户", "新建用户"]],
  ["/admin/audit", ["审计日志"]],
  ["/admin/config", ["配置", "路径"]],
  ["/admin/logs", ["运行日志"]],
  ["/admin/backup", ["备份", "立即备份"]],
  ["/admin/danger", ["危险操作", "熔断"]],
];

const results = [];

function check(name, body) {
  return results.push({ name, ...body });
}

async function visit(page, path, expected) {
  const errors = [];
  const failed = [];
  const onConsole = (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 200));
  };
  const onFail = (r) => failed.push(`${r.url().slice(-60)} ${r.failure()?.errorText ?? ""}`);
  page.on("console", onConsole);
  page.on("requestfailed", onFail);

  const res = await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 45000 });
  await page.waitForTimeout(700);

  const text = await page.locator("body").innerText();
  const missing = expected.filter((t) => !text.includes(t));

  // A blank shell is the classic SPA failure: status 200, nothing painted.
  const painted = text.trim().length > 40;

  page.off("console", onConsole);
  page.off("requestfailed", onFail);

  return check(path, {
    status: res?.status() ?? 0,
    painted,
    missing,
    errors: errors.filter((e) => !e.includes("favicon")),
    failed: failed.filter((f) => !f.includes("favicon")),
    chars: text.trim().length,
  });
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

for (const [path, expected] of PUBLIC_ROUTES) {
  await visit(page, path, expected);
}

if (PASSWORD) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#u", "operator");
  await page.fill("#p", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(app|admin)/, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1200);
  const landed = new URL(page.url()).pathname;
  check("login", { status: 200, painted: true, missing: [], errors: [], failed: [], chars: 0, landed });

  for (const [path, expected] of [...APP_ROUTES, ...ADMIN_ROUTES]) {
    await visit(page, path, expected);
  }
}

await browser.close();

let bad = 0;
for (const r of results) {
  const ok =
    r.status === 200 &&
    r.painted &&
    r.missing.length === 0 &&
    r.errors.length === 0 &&
    r.failed.length === 0;
  if (!ok) bad += 1;
  console.log(`${ok ? "OK  " : "FAIL"} ${r.name.padEnd(16)} ${r.status} ${r.chars}c`);
  if (r.landed) console.log(`      登录后落到: ${r.landed}`);
  if (r.missing.length) console.log(`      缺失文本: ${r.missing.join(", ")}`);
  if (r.errors.length) console.log(`      控制台: ${r.errors.join(" | ")}`);
  if (r.failed.length) console.log(`      请求失败: ${r.failed.join(" | ")}`);
  if (!r.painted) console.log(`      页面空白`);
}
console.log(`\n${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
