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
    trace: [], approvals: [], ...extra,
  };
}

test("empty queue loads without credentials or selecting/approving a report", async () => {
  const ui = harness([reply([])], "/director");
  await flush();
  assert.match(ui.el("queue-status").textContent, /No reports yet/);
  assert.equal(ui.el("report-status").hidden, true);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.headers.Authorization, undefined);
  assert.equal(ui.el("previous-page").disabled, true);
});

test("queue selection shows one report and a standalone report link", async () => {
  const data = report();
  const ui = harness([reply([data]), reply(data)], "/director");
  await flush();
  ui.click("queue-report-7");
  await flush();
  assert.equal(ui.location.href, "/director?report=7");
  assert.equal(ui.el("report-link").href, "/reports/7");
  assert.equal(ui.el("verdict").textContent, "NO-GO");
  assert.equal(ui.el("original-engineer_name").textContent, sample.engineer_name);
  assert.equal(ui.focused(), "report-heading");
  assert.ok(ui.requests.every((request) => request.options.method !== "POST"));
});

test("deep links load the queue and selected report independently", async () => {
  const ui = harness([reply([]), reply(report())], "/director?report=7");
  await flush();
  assert.equal(ui.el("report-result").hidden, false);
  assert.equal(ui.el("report-heading").textContent, "Report #7");
  assert.deepEqual(ui.requests.map((request) => request.url), ["/api/reports?limit=26&offset=0", "/api/reports/7"]);
});

test("pagination uses an extra row to determine the next page without losing a selected report", async () => {
  const rows = Array.from({ length: 26 }, (_, i) => report({ id: i + 1 }));
  const ui = harness([reply(rows), reply([report({ id: 27 })]), reply(rows)], "/director");
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

test("processing details poll every two seconds and stop when review is ready", async () => {
  const ui = harness([reply([]), reply(report({ status: "processing" })), reply(report())], "/director?report=7");
  await flush();
  await ui.poll();
  assert.match(ui.el("status-message").textContent, /Flight Director review/);
  assert.equal([...ui.timers.values()].filter((timer) => timer.delay === 2000).length, 0);
  assert.equal([...ui.timers.values()].filter((timer) => timer.delay === 5000).length, 1);
});

test("queue failure pauses only the queue and can be retried", async () => {
  const ui = harness([new Error("offline"), reply(report()), reply([])], "/director?report=7");
  await flush();
  assert.equal(ui.el("queue-error").hidden, false);
  assert.equal(ui.el("report-result").hidden, false);
  ui.click("refresh-queue");
  await flush();
  assert.equal(ui.el("queue-error").hidden, true);
});

test("a stale selected-report response cannot overwrite the newly selected report", async () => {
  let finish;
  const ui = harness([reply([report(), report({ id: 8 })]),
    () => new Promise((resolve) => { finish = resolve; }), reply(report({ id: 8 }))], "/director");
  await flush();
  ui.click("queue-report-7");
  ui.click("queue-report-8");
  await flush();
  finish({ ok: true, json: async () => report() });
  await flush();
  assert.equal(ui.el("report-heading").textContent, "Report #8");
  assert.equal(ui.el("report-link").href, "/reports/8");
});

test("trace has one card per step, preserves unavailable counts/costs, and displays metadata as text", async () => {
  const trace = [
    { step_name: "launch_limits", status: "completed", input_tokens: 0, output_tokens: 0,
      cost_usd: "0E-10", latency_ms: 1, details_json: {}, started_at: null, completed_at: null },
    { step_name: "draft", status: "processing", input_tokens: null, output_tokens: null,
      cost_usd: null, latency_ms: null, details_json: { round_number: 2, response_text: "<script>bad()</script>" } },
  ];
  const ui = harness([reply([]), reply(report({ trace }))], "/director?report=7");
  await flush();
  const cards = ui.el("trace-cards").children;
  assert.equal(cards.length, 2);
  assert.match(cards[0].textContent, /cost: \$0.00 USD/);
  assert.match(cards[1].textContent, /round 2/);
  assert.match(cards[1].textContent, /Input tokens: Unavailable/);
  assert.match(cards[1].textContent, /<script>/);
  assert.match(ui.el("cost-summary").textContent, /Known subtotal: \$0.00 USD/);
  assert.match(ui.el("cost-summary").textContent, /Recorded total .*: Unavailable/);
});

test("draft history, full findings, and approval audit remain inspectable", async () => {
  const latest = { id: 2, round_number: 2, quality_passed: true, content: "Reviewed briefing.",
    evaluator_feedback: { feedback: ["Ready for human review."] } };
  const ui = harness([reply([]), reply(report({ latest_briefing: latest,
    briefing_versions: [{ id: 1, round_number: 1, quality_passed: false, content: "<b>old draft</b>",
      evaluator_feedback: { feedback: ["Clarify wind."], canonical_issues: ["Restore verdict."] } }, latest],
    checks: [{ check_type: "injection", outcome: "flagged", reason: "Needs review",
      details_json: { findings: ["<script>instruction</script>"], error_reason: "invalid_output" } }],
    approvals: [{ briefing_version_id: 2, approved_by: "demo-flight-director",
      approved_at: "2026-11-17T20:03:00Z", acknowledged_flags_json: [3] }],
  }))], "/director?report=7");
  await flush();
  assert.match(ui.el("draft-history").textContent, /old draft/);
  assert.match(ui.el("draft-history").textContent, /Restore verdict/);
  assert.match(ui.el("check-cards").textContent, /Evaluation unresolved/);
  assert.match(ui.el("check-cards").textContent, /<script>/);
  assert.match(ui.el("approval-history").textContent, /demo-flight-director/);
  assert.match(ui.el("latest-feedback").textContent, /Ready for human review/);
});

test("page navigation stops queue and detail polling", async () => {
  const ui = harness([reply([]), reply(report({ status: "processing" }))], "/director?report=7");
  await flush();
  ui.windowEvents.pagehide();
  assert.equal(ui.timers.size, 0);
});
