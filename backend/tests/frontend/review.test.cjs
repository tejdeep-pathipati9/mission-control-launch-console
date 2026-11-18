const { test } = require("node:test");
const assert = require("node:assert/strict");
const { harness, sample } = require("./dom.cjs");
const flush = () => new Promise((resolve) => setImmediate(resolve));
const reply = (data) => ({ data });
const token = "synthetic-director-token-" + "d".repeat(32);
const identity = (role = "flight_director") => reply({ subject: `demo-${role}`, role, auth_mode: "local_demo" });

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

function ready(extra = {}) {
  const latest = { id: 21, report_id: 7, run_id: 10, round_number: 1,
    quality_passed: true, content: "Wind limit breached.", evaluator_feedback: { quality_passed: true } };
  const checks = ["injection", "missing_details", "panic"].map((check_type, i) => ({
    id: 101 + i, run_id: 10, check_type, outcome: i === 0 ? "flagged" : "clear",
    reason: i === 0 ? "Review embedded instructions." : "No findings.",
    details_json: { findings: i === 0 ? ["Suppression instruction."] : [], error_reason: null },
  }));
  return report({ checks, flags: checks.filter((check) => check.outcome === "flagged"),
    latest_briefing: latest, briefing_versions: [latest], ...extra });
}

async function connect(ui, value = token) {
  ui.el("director-token").value = value;
  await ui.submitForm("director-auth");
}

function approved(data = ready()) {
  return { ...data, status: "approved", approvals: [{ briefing_version_id: data.latest_briefing.id,
    approved_by: "demo-flight-director", approved_at: "2026-11-17T20:03:00Z", acknowledged_flags_json: [101] }] };
}

const savedApproval = (version = 21) => reply({ report_id: 7, run_id: 10, status: "approved",
  approval: { briefing_version_id: version } });

test("briefing uses its exact draft summary while the full saved version remains accessible", async () => {
  const data = ready();
  data.latest_briefing.content = "Anomaly summary: Wind is above its launch limit. Verify the sensor and resolve the flagged instructions.\nSubsystem: weather\nFlagged checks: retained in full.";
  data.trace = [{ id: 1, run_id: 10, step_name: "draft", status: "completed", latency_ms: 8089,
    input_tokens: 1890, output_tokens: 550, cost_usd: null,
    details_json: { round_number: 1, validated_output: { summary: "Wind is above its launch limit. Verify the sensor and resolve the flagged instructions." } } }];
  const ui = harness([reply(data)], "/reports/7");
  await flush();
  assert.match(ui.el("briefing-content").textContent, /^Wind is above/);
  assert.doesNotMatch(ui.el("briefing-content").textContent, /Subsystem:|Flagged checks:/);
  assert.equal(ui.el("briefing-full-content").textContent, data.latest_briefing.content);
  assert.equal(ui.el("briefing-excerpt").hidden, true);
  assert.match(ui.el("trace-cards").textContent, /8\.1 s/);
  assert.match(ui.el("trace-cards").textContent, /1,890 \/ 550/);
});

test("a verbose legacy briefing gets a labelled overview with all original text retained", async () => {
  const data = ready();
  data.latest_briefing.content = "Observation ".repeat(120) + "Retain the complete original.";
  const ui = harness([reply(data)], "/reports/7");
  await flush();
  assert.equal(ui.el("briefing-excerpt").hidden, false);
  assert.match(ui.el("briefing-content").textContent, /^Readings breach launch limits \(NO-GO\)/);
  assert.equal(ui.el("briefing-heading").textContent, "Report overview");
  assert.match(ui.el("briefing-excerpt").textContent, /From saved readings and checks/);
  assert.equal(ui.el("briefing-full-content").textContent, data.latest_briefing.content);
  assert.equal(ui.el("verdict").textContent, "NO-GO");
  assert.match(ui.el("verdict").className, /danger/);
});

for (const verdict of ["go", "no_go"]) {
  test(`the missing-context overview uses the saved ${verdict} verdict rather than the old prose`, async () => {
    const data = ready({ verdict });
    data.checks[0].outcome = "clear";
    data.checks[0].details_json.findings = [];
    data.checks[1].outcome = "flagged";
    data.checks[1].details_json.findings = ["Observation time and equipment are missing."];
    data.latest_briefing.content = "Weather subsystem report. The canonical verdict is GO. The free-text note field is unintelligible.";
    const original = data.latest_briefing.content;
    const ui = harness([reply(data)], "/reports/7");
    await flush();
    const expected = verdict === "go" ? "Readings meet launch limits (GO)." : "Readings breach launch limits (NO-GO).";
    assert.equal(ui.el("briefing-content").textContent, `${expected} Missing details remain flagged for review. Ask the reporting engineer to fill the gaps listed in the checks.`);
    assert.equal(ui.el("briefing-full-content").textContent, original);
    assert.equal(ui.el("approve-briefing").disabled, true);
    assert.equal(ui.requests.filter(request => request.options.method === "POST").length, 0);
  });
}

test("the overview cannot describe incomplete checks as clear", async () => {
  const data = ready({ status: "failed" });
  data.checks[0].outcome = "error";
  data.latest_briefing.content = "Observation ".repeat(100);
  const ui = harness([reply(data)], "/reports/7");
  await flush();
  assert.match(ui.el("briefing-content").textContent, /Required checks are incomplete/);
  assert.equal(ui.el("approval-form").hidden, true);
  assert.doesNotMatch(ui.el("briefing-content").textContent, /No checks are flagged/);
});

test("another attempt or round cannot replace the current briefing summary", async () => {
  const data = ready();
  data.trace = [
    { run_id: 99, step_name: "draft", status: "completed", details_json: { round_number: 1, validated_output: { summary: "Wrong attempt" } } },
    { run_id: 10, step_name: "draft", status: "completed", details_json: { round_number: 2, validated_output: { summary: "Wrong round" } } },
  ];
  const ui = harness([reply(data)], "/reports/7");
  await flush();
  assert.equal(ui.el("briefing-content").textContent, data.latest_briefing.content);
});

test("all checks share one table and expanded findings survive a same-version refresh", async () => {
  const data = ready();
  const ui = harness([reply(data), identity(), reply(data)], "/reports/7");
  await flush();
  assert.equal(ui.el("check-cards").children.length, 3);
  assert.equal(ui.el("findings-injection").open, false);
  ui.el("findings-injection").open = true;
  await connect(ui);
  ui.change("ack-check-101", true);
  ui.click("refresh-report");
  await flush();
  assert.equal(ui.el("findings-injection").open, true);
  assert.equal(ui.el("ack-check-101").checked, true);
  assert.equal(ui.el("approve-briefing").disabled, false);
});

test("a historical failed report shows unavailable stages and no approval form", async () => {
  const data = report({ status: "failed", subsystem: null, checks: [],
    current_run: { failure_message: "Model processing is not implemented in this milestone." } });
  const ui = harness([reply(data)], "/reports/7");
  await flush();
  assert.equal(ui.el("subsystem").textContent, "Unavailable");
  assert.equal(ui.el("checks-empty").hidden, false);
  assert.equal(ui.el("check-cards").children.length, 3);
  assert.equal(ui.el("approval-form").hidden, true);
  assert.equal(ui.el("director-access").hidden, true);
  assert.match(ui.el("failure-detail").textContent, /not implemented in this milestone/);
  assert.equal(ui.el("queue-body"), undefined);
  assert.equal(ui.requests.length, 1);
});

test("trace has one card per step, preserves unavailable counts/costs, and displays metadata as text", async () => {
  const trace = [
    { step_name: "launch_limits", status: "completed", input_tokens: 0, output_tokens: 0,
      cost_usd: "0E-10", latency_ms: 1, details_json: {}, started_at: null, completed_at: null },
    { step_name: "draft", status: "processing", input_tokens: null, output_tokens: null,
      cost_usd: null, latency_ms: null, details_json: { round_number: 2, response_text: "<script>bad()</script>" } },
  ];
  const ui = harness([reply(report({ trace }))], "/reports/7");
  await flush();
  const cards = ui.el("trace-cards").children;
  assert.equal(cards.length, 2);
  assert.match(cards[0].textContent, /\$0.00/);
  assert.match(cards[1].textContent, /round 2/);
  assert.match(cards[1].textContent, /— \/ —/);
  assert.match(cards[1].textContent, /<script>/);
  assert.match(ui.el("cost-summary").textContent, /Known subtotal: \$0.00 USD/);
  assert.match(ui.el("cost-summary").textContent, /Unavailable/);
  assert.equal(cards[1].children[0].open, false);
});

test("draft history, full findings, and approval audit remain inspectable", async () => {
  const latest = { id: 2, round_number: 2, quality_passed: true, content: "Reviewed briefing.",
    evaluator_feedback: { feedback: ["Ready for human review."] } };
  const ui = harness([reply(report({ latest_briefing: latest,
    briefing_versions: [{ id: 1, round_number: 1, quality_passed: false, content: "<b>old draft</b>",
      evaluator_feedback: { feedback: ["Clarify wind."], canonical_issues: ["Restore verdict."] } }, latest],
    checks: [{ check_type: "injection", outcome: "flagged", reason: "Needs review",
      details_json: { findings: ["<script>instruction</script>"], error_reason: "invalid_output" } }],
    approvals: [{ briefing_version_id: 2, approved_by: "demo-flight-director",
      approved_at: "2026-11-17T20:03:00Z", acknowledged_flags_json: [3] }],
  }))], "/reports/7");
  await flush();
  assert.match(ui.el("draft-history").textContent, /old draft/);
  assert.match(ui.el("draft-history").textContent, /Restore verdict/);
  assert.match(ui.el("check-cards").textContent, /Evaluation unresolved/);
  assert.match(ui.el("check-cards").textContent, /<script>/);
  assert.match(ui.el("approval-history").textContent, /demo-flight-director/);
  assert.match(ui.el("latest-feedback").textContent, /Ready for human review/);
});

test("director approval requires explicit flags, sends only version IDs, and preserves NO-GO", async () => {
  const data = ready();
  const saved = approved(data);
  const ui = harness([reply(data), identity(), savedApproval(), reply(saved)], "/reports/7");
  await flush();
  assert.equal(ui.el("approve-briefing").disabled, true);
  await connect(ui);
  assert.equal(ui.el("director-token").value, "");
  assert.equal(ui.el("approve-briefing").disabled, true);
  ui.change("ack-check-101", true);
  assert.equal(ui.el("approve-briefing").disabled, false);
  await ui.submitForm("approval-form");
  await flush();
  const post = ui.requests.find((request) => request.options.method === "POST");
  assert.equal(post.url, "/api/reports/7/approve");
  assert.deepEqual(JSON.parse(post.options.body), { run_id: 10, briefing_version_id: 21, acknowledged_check_result_ids: [101] });
  assert.equal(post.options.headers.Authorization, `Bearer ${token}`);
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.equal(ui.el("verdict").textContent, "NO-GO");
  assert.match(ui.el("approval-history").textContent, /demo-flight-director/);
  assert.match(ui.el("approval-status").textContent, /already been approved/);
  assert.ok(ui.requests.filter((request) => request.url.startsWith("/api/reports") && request.options.method !== "POST")
    .every((request) => request.options.headers.Authorization === undefined));
  assert.ok(!ui.el("auth-status").textContent.includes(token));
  assert.ok(!ui.location.href.includes(token));
});

test("an authenticated engineer can inspect but cannot enable approval", async () => {
  const ui = harness([reply(ready()), identity("engineer")], "/reports/7");
  await flush();
  await connect(ui);
  ui.change("ack-check-101", true);
  await ui.submitForm("approval-form");
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.equal(ui.el("ack-check-101").disabled, true);
  assert.match(ui.el("auth-status").textContent, /Flight Director access is required/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 0);
});

for (const scenario of ["processing", "failed", "quality_failed", "malformed_check", "missing_check", "stale_version"]) {
  test(`${scenario} cannot be approved even with a director identity`, async () => {
    const data = ready();
    if (["processing", "failed"].includes(scenario)) data.status = scenario;
    if (scenario === "quality_failed") data.latest_briefing.quality_passed = false;
    if (scenario === "malformed_check") data.checks[0].details_json.error_reason = "invalid_output";
    if (scenario === "missing_check") data.checks.pop();
    if (scenario === "stale_version") data.latest_briefing.run_id = 9;
    const ui = harness([reply(data), identity()], "/reports/7");
    await flush();
    await connect(ui);
    ui.change("ack-check-101", true);
    await ui.submitForm("approval-form");
    assert.equal(ui.el("approve-briefing").disabled, true);
    assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 0);
  });
}

test("all flags must be checked and can be unchecked before approval", async () => {
  const data = ready();
  data.checks[1].outcome = "flagged";
  data.checks[1].details_json.findings = ["Missing observation time."];
  const ui = harness([reply(data), identity()], "/reports/7");
  await flush();
  await connect(ui);
  ui.change("ack-check-101", true);
  assert.equal(ui.el("approve-briefing").disabled, true);
  ui.change("ack-check-102", true);
  assert.equal(ui.el("approve-briefing").disabled, false);
  ui.change("ack-check-101", false);
  assert.equal(ui.el("approve-briefing").disabled, true);
});

test("a report with no flags needs no fabricated acknowledgement IDs", async () => {
  const data = ready();
  data.checks[0].outcome = "clear";
  data.checks[0].details_json.findings = [];
  const ui = harness([reply(data), identity(), savedApproval(), reply(approved(data))], "/reports/7");
  await flush();
  await connect(ui);
  assert.equal(ui.el("approve-briefing").disabled, false);
  await ui.submitForm("approval-form");
  assert.deepEqual(JSON.parse(ui.requests.find((request) => request.options.method === "POST").options.body).acknowledged_check_result_ids, []);
});

test("a stale-approval conflict reloads the report and requires new acknowledgements", async () => {
  const revised = ready();
  revised.latest_briefing.id = 22;
  const ui = harness([reply(ready()), identity(),
    { status: 409, data: { detail: { code: "stale_briefing" } } }, reply(revised)], "/reports/7");
  await flush();
  await connect(ui);
  ui.change("ack-check-101", true);
  await ui.submitForm("approval-form");
  assert.equal(ui.el("ack-check-101").checked, false);
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.match(ui.el("approval-message").textContent, /report changed/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 1);
});

test("an uncertain save is checked with GET and never automatically posted again", async () => {
  const ui = harness([reply(ready()), identity(), new Error("Connection lost"), reply(approved())], "/reports/7");
  await flush();
  await connect(ui);
  ui.change("ack-check-101", true);
  await ui.submitForm("approval-form");
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.match(ui.el("approval-status").textContent, /already been approved/);
  assert.match(ui.el("approval-message").textContent, /could not be confirmed/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 1);
});

test("failed refresh makes an otherwise eligible briefing unapprovable", async () => {
  const data = ready();
  data.checks[0].outcome = "clear";
  const ui = harness([reply(data), identity(), new Error("offline")], "/reports/7");
  await flush();
  await connect(ui);
  assert.equal(ui.el("approve-briefing").disabled, false);
  ui.click("refresh-report");
  await flush();
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.match(ui.el("approval-status").textContent, /Refresh this report/);
});

test("authentication failure clears entered credentials and cannot grant a role", async () => {
  const ui = harness([reply(ready()), { status: 401, data: { detail: "Invalid" } }], "/reports/7");
  await flush();
  await connect(ui);
  assert.equal(ui.el("director-token").value, "");
  assert.match(ui.el("auth-status").textContent, /Could not verify/);
  assert.equal(ui.el("approve-briefing").disabled, true);
});

test("disconnect invalidates a late identity response and clears acknowledgements", async () => {
  let finish;
  const ui = harness([reply(ready()), () => new Promise((resolve) => { finish = resolve; })], "/reports/7");
  await flush();
  const pending = connect(ui);
  ui.click("disconnect-director");
  finish({ ok: true, json: async () => ({ subject: "demo-flight-director", role: "flight_director" }) });
  await pending;
  assert.match(ui.el("auth-status").textContent, /Signed out/);
  assert.equal(ui.el("approve-briefing").disabled, true);
});

test("returning through browser history requires authentication again", async () => {
  const data = ready();
  data.checks[0].outcome = "clear";
  const ui = harness([reply(data), identity(), reply(data)], "/reports/7");
  await flush();
  await connect(ui);
  assert.equal(ui.el("approve-briefing").disabled, false);
  ui.windowEvents.pagehide();
  ui.windowEvents.pageshow({ persisted: true });
  await flush();
  assert.equal(ui.el("approve-briefing").disabled, true);
  assert.match(ui.el("auth-status").textContent, /Signed out/);
  assert.equal(ui.el("director-token").value, "");
  assert.equal(ui.requests.filter((request) => request.url === "/api/auth/me").length, 1);
});
