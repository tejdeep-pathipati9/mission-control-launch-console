const { test } = require("node:test");
const assert = require("node:assert/strict");
const { harness, sample } = require("./dom.cjs");


function detail(status = "processing", extra = {}) {
  return {
    ...sample, id: 7, status, verdict: "no_go", violations_json: ["Wind exceeds 25 knots."],
    subsystem: null, severity: "high", brief_by_at: "2026-11-17T20:05:00Z",
    checks: [], latest_briefing: null, current_run: {},
    trace: [], briefing_versions: [], approvals: [], pipeline_runs: [],
    costs: { cost_complete: false, known_cost_usd: "0", total_cost_usd: null }, ...extra,
  };
}
const accepted = () => ({ status: 202, data: { report_id: 7, status: "processing" } });
const reply = (data) => ({ data });
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("empty and whitespace fields show linked errors without creating a report", async () => {
  const ui = harness();
  ui.fill({ ...sample, engineer_name: " ", wind_knots: "", temp_c: "Infinity" });
  await ui.submit();
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.el("form-errors").hidden, false);
  assert.equal(ui.el("engineer_name").attributes["aria-invalid"], "true");
  assert.equal(ui.el("wind_knots").attributes["aria-invalid"], "true");
  assert.equal(ui.focused(), "form-errors");
  assert.equal(ui.el("note").value, sample.note);
});

for (const [field, value] of [["wind_knots", "-0.001"], ["temp_c", "-273.15001"], ["fuel_pct", "-0.001"]]) {
  test(`impossible ${field} is rejected without a request`, async () => {
    const ui = harness();
    ui.fill({ ...sample, [field]: value });
    await ui.submit();
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.el(field).attributes["aria-invalid"], "true");
    assert.match(ui.el(`${field}-error`).textContent, /or higher/);
  });
}

test("physical lower bounds are inclusive and still submit for launch evaluation", async () => {
  const ui = harness([accepted()]);
  ui.fill({ ...sample, wind_knots: "0", temp_c: "-273.15", fuel_pct: "0" });
  await ui.submit();
  const body = JSON.parse(ui.requests[0].options.body);
  assert.equal(body.wind_knots, 0);
  assert.equal(body.temp_c, -273.15);
  assert.equal(body.fuel_pct, 0);
});

test("successful submission navigates to a new report page and blocks duplicate POSTs", async () => {
  const ui = harness([accepted()]);
  ui.fill({ ...sample, engineer_name: " Avery Chen ", wind_knots: "26", temp_c: "3", fuel_pct: "101" });
  const submission = ui.submit();
  await ui.submit();
  await submission;
  const body = JSON.parse(ui.requests[0].options.body);
  assert.equal(body.wind_knots, 26);
  assert.equal(body.temp_c, 3);
  assert.equal(body.fuel_pct, 101);
  assert.equal(body.engineer_name, "Avery Chen");
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.el("report-fields").disabled, true);
  assert.deepEqual(ui.navigations, ["/reports/7"]);
  assert.equal(ui.el("report-status"), undefined);
  assert.equal(ui.timers.size, 0);
});

test("the report page polls independently and stops when ready", async () => {
  const ui = harness([reply(detail()), reply(detail("pending_review"))], "/reports/7");
  await flush();
  assert.equal(ui.el("report-form"), undefined);
  assert.equal(ui.el("verdict").textContent, "NO-GO");
  assert.equal(ui.el("subsystem").textContent, "Pending");
  assert.match(ui.el("brief-by").textContent, /UTC/);
  await ui.poll();
  assert.match(ui.el("status-message").textContent, /Ready for review/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 0);
  assert.equal(ui.timers.size, 0);
});

test("server validation preserves inputs and makes fields editable for correction", async () => {
  const ui = harness([{ status: 422, data: { detail: [{ loc: ["body", "note"], msg: "Explain the anomaly." }] } }]);
  ui.fill();
  await ui.submit();
  assert.equal(ui.el("report-fields").disabled, false);
  assert.equal(ui.el("note").value, sample.note);
  assert.equal(ui.el("note").attributes["aria-invalid"], "true");
  assert.equal(ui.el("note-error").textContent, "Explain the anomaly.");
  assert.equal(ui.timers.size, 0);
});

test("an unconfirmed POST is never retried automatically", async () => {
  const ui = harness([new TypeError("network down")]);
  ui.fill();
  await ui.submit();
  assert.match(ui.el("error-list").textContent, /may have been saved/);
  assert.equal(ui.el("report-fields").disabled, false);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.timers.size, 0);
});

test("server submission errors use text and preserve the report", async () => {
  const ui = harness([{ status: 503, data: { detail: "Storage temporarily unavailable." } }]);
  ui.fill();
  await ui.submit();
  assert.match(ui.el("error-list").textContent, /Storage temporarily unavailable/);
  assert.equal(ui.el("note").value, sample.note);
  assert.equal(ui.requests.length, 1);
});

test("retrying a failed status request only fetches the already-saved report", async () => {
  const ui = harness([new TypeError("offline"), reply(detail("failed", {
    current_run: { failure_message: "Processing was interrupted by an application restart." },
  }))], "/reports/7");
  await flush();
  assert.equal(ui.el("poll-error").hidden, false);
  assert.match(ui.el("poll-error-message").textContent, /saved report is unchanged/);
  assert.equal(ui.timers.size, 0);
  ui.click("retry-status");
  await flush();
  assert.equal(ui.el("poll-error").hidden, true);
  assert.match(ui.el("failure-detail").textContent, /interrupted/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 0);
  assert.equal(ui.requests[1].url, "/api/reports/7");
  assert.equal(ui.timers.size, 0);
});

test("a report link resumes tracking with GET, not a second submission", async () => {
  const ui = harness([reply(detail("pending_review"))], "/reports/7");
  await flush();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, "/api/reports/7");
  assert.equal(ui.requests[0].options.method, undefined);
  assert.equal(ui.el("original-engineer_name").textContent, sample.engineer_name);
  assert.equal(ui.el("report-form"), undefined);
  assert.equal(ui.el("report-link").href, "/reports/7");
});

for (const id of ["0", "-1", "<script>", "NaN", "9007199254740993"]) {
  test(`invalid report link ${id} does not send a request`, () => {
    const ui = harness([], `/reports/${id}`);
    assert.equal(ui.requests.length, 0);
    assert.match(ui.el("poll-error-message").textContent, /link is invalid/);
  });
}

test("a missing report stops tracking and allows starting a new form", async () => {
  const ui = harness([{ status: 404, data: { detail: "Report not found." } }], "/reports/7");
  await flush();
  assert.equal(ui.el("retry-status").hidden, true);
  assert.match(ui.el("poll-error-message").textContent, /could not be found/);
  assert.doesNotMatch(ui.el("status-message").textContent, /Report saved/);
  assert.equal(ui.el("new-report").href, "/");
  assert.equal(ui.el("report-form"), undefined);
});

test("leaving a report page cancels its GET and ignores late results", async () => {
  let finish;
  const ui = harness([() => new Promise((resolve) => { finish = resolve; })], "/reports/7");
  ui.windowEvents.pagehide();
  assert.equal(ui.requests[0].options.signal.aborted, true);
  finish({ ok: true, json: async () => detail("pending_review") });
  await flush();
  assert.equal(ui.el("report-result").hidden, true);
  assert.equal(ui.timers.size, 0);
});

test("Back to a successfully submitted form clears it without another POST", async () => {
  const ui = harness([accepted()]);
  ui.fill();
  await ui.submit();
  ui.windowEvents.pagehide();
  ui.windowEvents.pageshow({ persisted: true });
  assert.equal(ui.el("engineer_name").value, "");
  assert.equal(ui.el("report-fields").disabled, false);
  assert.equal(ui.el("form-errors").hidden, true);
  assert.equal(ui.requests.length, 1);
});

test("returning from the back-forward cache resumes polling", async () => {
  const ui = harness([reply(detail()), reply(detail("pending_review"))], "/reports/7");
  await flush();
  ui.windowEvents.pagehide();
  assert.equal(ui.timers.size, 0);
  ui.windowEvents.pageshow({ persisted: true });
  await flush();
  assert.match(ui.el("status-message").textContent, /Ready for review/);
  assert.equal(ui.requests.length, 2);
});

test("returning during an interrupted POST restores the form without resubmitting", async () => {
  let finish;
  const ui = harness([() => new Promise((resolve) => { finish = resolve; })]);
  ui.fill();
  const pending = ui.submit();
  ui.windowEvents.pagehide();
  ui.windowEvents.pageshow({ persisted: true });
  assert.equal(ui.el("report-fields").disabled, false);
  assert.match(ui.el("error-list").textContent, /may have been saved/);
  finish({ ok: true, json: async () => ({ report_id: 7 }) });
  await pending;
  assert.equal(ui.requests.length, 1);
  assert.deepEqual(ui.navigations, []);
});

for (const status of ["pending_review", "approved", "failed"]) {
  test(`${status} stops polling and keeps model content as text`, async () => {
    const untrusted = '<script>alert("override")</script>';
    const ui = harness([reply(detail(status, {
      latest_briefing: { content: untrusted, quality_passed: status !== "failed" },
      checks: [{ check_type: "injection", outcome: "flagged", reason: untrusted }],
    }))], "/reports/7");
    await flush();
    assert.equal(ui.el("briefing-content").textContent, untrusted);
    assert.match(ui.el("check-cards").textContent, /<script>/);
    assert.equal(ui.el("briefing-content").children.length, 0);
    assert.equal(ui.el("verdict").textContent, "NO-GO");
    assert.equal(ui.timers.size, 0);
    if (status === "failed") assert.match(ui.el("briefing-quality").textContent, /Draft/);
  });
}

test("an unresolved fail-safe injection flag is visible on a failed report", async () => {
  const reason = "Hidden-instructions check returned an invalid reply; the note requires human review.";
  const ui = harness([reply(detail("failed", {
    checks: [{ check_type: "injection", outcome: "flagged", reason,
               details_json: { error_reason: "invalid_output", review_required: true } }],
  }))], "/reports/7");
  await flush();
  assert.match(ui.el("check-cards").textContent, /Unresolved.*human review/);
  assert.match(ui.el("checks-summary").textContent, /0 of 3 checks completed; 1 could not complete/);
  assert.match(ui.el("status-message").textContent, /Processing failed/);
});
