const { test } = require("node:test");
const assert = require("node:assert/strict");
const { harness, sample } = require("./dom.cjs");
const flush = () => new Promise((resolve) => setImmediate(resolve));
const reply = (data) => ({ data });
function report(extra = {}) {
  const costs = { total_cost_usd: null, known_cost_usd: "0", cost_complete: false,
    total_input_tokens: null, total_output_tokens: null };
  return {
    ...sample, id: 7, status: "pending_review", verdict: "no_go", severity: "high",
    subsystem: "weather", violations_json: ["wind_knots must be <= 25"],
    brief_by_at: "2026-11-17T20:05:00Z", costs,
    current_run: { ...costs, id: 10, status: "pending_review", completed_at: "2026-11-17T20:02:00Z", failure_reason: null },
    flags: [], checks: [], briefing_versions: [], latest_briefing: null,
    trace: [], approvals: [], pipeline_runs: [], ...extra,
  };
}

test("empty queue loads without credentials or selecting/approving a report", async () => {
  const ui = harness([reply([])], "/reports");
  await flush();
  assert.match(ui.el("queue-status").textContent, /No reports yet/);
  assert.equal(ui.el("report-status"), undefined);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.headers.Authorization, undefined);
  assert.equal(ui.el("previous-page").disabled, true);
});

test("queue distinguishes unavailable and partial costs from a recorded zero", async () => {
  const data = [
    report({ id: 1 }),
    report({ id: 2, costs: { cost_complete: false, known_cost_usd: "0.003" } }),
    report({ id: 3, costs: { cost_complete: true, total_cost_usd: "0" } }),
    report({ id: 4, status: "processing", brief_by_at: null }),
  ];
  const ui = harness([reply(data)], "/reports");
  await flush();
  const rows = ui.el("queue-body").children;
  assert.match(rows[0].children[6].textContent, /Unavailable/);
  assert.ok(!rows[0].children[6].textContent.includes("$0"));
  assert.match(rows[1].children[6].textContent, /\$0\.003 Partial cost/);
  assert.equal(rows[2].children[6].textContent, "$0.00");
  assert.match(rows[3].children[6].textContent, /Pending/);
  assert.equal(rows[3].children[4].textContent, "Pending");
});

test("report links navigate to dedicated review pages without an inline click handler", async () => {
  const ui = harness([reply([report()])], "/reports");
  await flush();
  assert.equal(ui.el("queue-report-7").href, "/reports/7");
  assert.equal(ui.el("queue-report-7").events.click, undefined);
  assert.equal(ui.el("report-result"), undefined);
  assert.equal(ui.el("approval-form"), undefined);
  assert.equal(ui.el("director-token"), undefined);
  assert.equal(ui.requests.length, 1);
});

test("pagination uses an extra row to determine the next page with independent queue pages", async () => {
  const rows = Array.from({ length: 26 }, (_, i) => report({ id: i + 1 }));
  const ui = harness([reply(rows), reply([report({ id: 27 })]), reply(rows)], "/reports");
  await flush();
  assert.equal(ui.el("queue-body").children.length, 25);
  assert.equal(ui.el("next-page").disabled, false);
  ui.click("next-page");
  await flush();
  assert.equal(ui.requests[1].url, "/api/reports?limit=26&offset=25");
  assert.equal(ui.el("next-page").disabled, true);
  ui.click("previous-page");
  await flush();
  assert.equal(ui.requests[2].url, "/api/reports?limit=26&offset=0");
});

test("queue failure pauses polling and refresh resumes GET-only updates", async () => {
  const ui = harness([new Error("offline"), reply([report()]), reply([])], "/reports");
  await flush();
  assert.equal(ui.el("queue-error").hidden, false);
  ui.click("refresh-queue");
  await flush();
  assert.equal(ui.el("queue-error").hidden, true);
  await ui.poll(5000);
  assert.match(ui.el("queue-status").textContent, /No reports yet/);
  assert.ok(ui.requests.every(request => request.options.method === undefined));
});

test("leaving the queue cancels polling and history return reloads the list", async () => {
  const ui = harness([reply([report()]), reply([])], "/reports");
  await flush();
  ui.windowEvents.pagehide();
  assert.equal(ui.timers.size, 0);
  ui.windowEvents.pageshow({ persisted: true });
  await flush();
  assert.match(ui.el("queue-status").textContent, /No reports yet/);
  assert.equal(ui.requests.length, 2);
});
