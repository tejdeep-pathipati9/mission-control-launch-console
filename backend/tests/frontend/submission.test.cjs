const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = process.env.UI_STATIC_DIR || path.resolve(__dirname, "../../app/static");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "assets/submission.js"), "utf8");
const names = ["engineer_name", "station", "wind_knots", "temp_c", "fuel_pct", "note"];
const sample = { engineer_name: "Avery Chen", station: "Weather", wind_knots: "26", temp_c: "4", fuel_pct: "98", note: "Backup sensor agrees." };

// In-memory DOM surface for controller tests. This is not a visual browser test.
function harness(responses = [], search = "") {
  const elements = new Map();
  const requests = [];
  const timers = new Map();
  const windowEvents = {};
  let timerId = 0;
  let focused;
  class Element {
    constructor(id = "", attrs = "") {
      this.id = id;
      this.value = "";
      this.hidden = /\bhidden\b/.test(attrs);
      this.disabled = /\bdisabled\b/.test(attrs);
      this.validity = { badInput: false };
      this.attributes = {};
      this.children = [];
      this.events = {};
      this.text = "";
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map((child) => child.textContent).join(" "); }
    set innerHTML(value) { throw new Error("Untrusted HTML must never be rendered"); }
    append(child) { this.children.push(child); }
    replaceChildren(...children) { this.text = ""; this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, callback) { this.events[name] = callback; }
    focus() { focused = this.id; }
    reset() { for (const name of names) elements.get(name).value = ""; }
  }
  for (const match of html.matchAll(/<[a-z][a-z0-9]*\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    elements.set(match[2], new Element(match[2], match[1]));
  }
  const location = { search };
  const context = {
    document: {
      querySelector: (selector) => {
        const result = elements.get(selector.slice(1));
        assert.ok(result, `Unknown element ${selector}`);
        return result;
      },
      getElementById: (id) => elements.get(id),
      createElement: () => new Element(),
    },
    window: { addEventListener: (name, callback) => { windowEvents[name] = callback; } },
    location,
    history: { replaceState: (_state, _title, url) => {
      location.href = url;
      location.search = new URL(url, "http://local.test").search;
    } },
    URLSearchParams, AbortController,
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout: (id) => timers.delete(id),
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(responses.length, `Unexpected request ${url}`);
      const next = responses.shift();
      if (next instanceof Error) throw next;
      if (typeof next === "function") return next(url, options);
      return { ok: (next.status || 200) < 400, status: next.status || 200, json: async () => next.data };
    },
  };
  vm.runInNewContext(script, context);
  return {
    el: (id) => elements.get(id), requests, timers, location, windowEvents,
    focused: () => focused,
    fill: (values = sample) => { for (const [key, value] of Object.entries(values)) elements.get(key).value = value; },
    submit: () => elements.get("report-form").events.submit({ preventDefault() {} }),
    click: (id) => elements.get(id).events.click(),
    poll: async () => {
      const entry = [...timers].find(([, timer]) => timer.delay === 2000);
      assert.ok(entry, "No scheduled poll");
      timers.delete(entry[0]);
      await entry[1].callback();
    },
  };
}

function detail(status = "processing", extra = {}) {
  return {
    ...sample, id: 7, status, verdict: "no_go", violations_json: ["Wind exceeds 25 knots."],
    subsystem: null, severity: "high", brief_by_at: "2026-11-17T20:05:00Z",
    checks: [], latest_briefing: null, current_run: {}, ...extra,
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

test("out-of-limit readings submit as numbers; duplicates are blocked and polling stops on readiness", async () => {
  const ui = harness([accepted(), reply(detail()), reply(detail("pending_review"))]);
  ui.fill({ ...sample, engineer_name: " Avery Chen ", wind_knots: "26", temp_c: "3", fuel_pct: "101" });
  const submission = ui.submit();
  await ui.submit();
  await submission;
  const body = JSON.parse(ui.requests[0].options.body);
  assert.equal(body.wind_knots, 26);
  assert.equal(body.temp_c, 3);
  assert.equal(body.fuel_pct, 101);
  assert.equal(body.engineer_name, "Avery Chen");
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 1);
  assert.equal(ui.el("report-fields").disabled, true);
  assert.equal(ui.el("report-status").hidden, false);
  assert.equal(ui.el("verdict").textContent, "NO-GO");
  assert.equal(ui.el("subsystem").textContent, "Awaiting classification");
  assert.match(ui.el("brief-by").textContent, /UTC/);
  assert.equal(ui.location.href, "/?report=7#report-status");
  await ui.poll();
  assert.match(ui.el("status-message").textContent, /Flight Director review/);
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
  const ui = harness([accepted(), new TypeError("offline"), reply(detail("failed", {
    current_run: { failure_message: "Processing was interrupted by an application restart." },
  }))]);
  ui.fill();
  await ui.submit();
  assert.equal(ui.el("poll-error").hidden, false);
  assert.match(ui.el("poll-error-message").textContent, /saved report is unchanged/);
  assert.equal(ui.timers.size, 0);
  ui.click("retry-status");
  await flush();
  assert.equal(ui.el("poll-error").hidden, true);
  assert.match(ui.el("status-message").textContent, /interrupted/);
  assert.equal(ui.requests.filter((request) => request.options.method === "POST").length, 1);
  assert.equal(ui.requests[2].url, "/api/reports/7");
  assert.equal(ui.timers.size, 0);
});

test("a report link resumes tracking with GET, not a second submission", async () => {
  const ui = harness([reply(detail("pending_review"))], "?report=7");
  await flush();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, "/api/reports/7");
  assert.equal(ui.requests[0].options.method, undefined);
  assert.equal(ui.el("engineer_name").value, sample.engineer_name);
  assert.equal(ui.el("report-fields").disabled, true);
});

for (const id of ["0", "-1", "<script>", "NaN", "9007199254740993"]) {
  test(`invalid report link ${id} does not send a request`, () => {
    const ui = harness([], `?report=${encodeURIComponent(id)}`);
    assert.equal(ui.requests.length, 0);
    assert.match(ui.el("error-list").textContent, /link is invalid/);
  });
}

test("a missing report stops tracking and allows starting a new form", async () => {
  const ui = harness([{ status: 404, data: { detail: "Report not found." } }], "?report=7");
  await flush();
  assert.equal(ui.el("retry-status").hidden, true);
  assert.match(ui.el("poll-error-message").textContent, /could not be found/);
  assert.doesNotMatch(ui.el("status-message").textContent, /Report saved/);
  ui.click("new-report");
  assert.equal(ui.el("report-fields").disabled, false);
  assert.equal(ui.el("report-status").hidden, true);
  assert.equal(ui.location.href, "/");
});

test("a stale GET cannot overwrite a fresh form after New report", async () => {
  let finish;
  const ui = harness([() => new Promise((resolve) => { finish = resolve; })], "?report=7");
  ui.click("new-report");
  finish({ ok: true, json: async () => detail("pending_review") });
  await flush();
  assert.equal(ui.el("report-status").hidden, true);
  assert.equal(ui.el("engineer_name").value, "");
  assert.equal(ui.timers.size, 0);
});

test("returning from the back-forward cache resumes polling", async () => {
  const ui = harness([reply(detail()), reply(detail("pending_review"))], "?report=7");
  await flush();
  ui.windowEvents.pagehide();
  assert.equal(ui.timers.size, 0);
  ui.windowEvents.pageshow({ persisted: true });
  await flush();
  assert.match(ui.el("status-message").textContent, /Flight Director review/);
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
  assert.equal(ui.el("report-status").hidden, true);
});

for (const status of ["pending_review", "approved", "failed"]) {
  test(`${status} stops polling and keeps model content as text`, async () => {
    const untrusted = '<script>alert("override")</script>';
    const ui = harness([reply(detail(status, {
      latest_briefing: { content: untrusted, quality_passed: status !== "failed" },
      checks: [{ check_type: "injection", outcome: "flagged", reason: untrusted }],
    }))], "?report=7");
    await flush();
    assert.equal(ui.el("briefing-content").textContent, untrusted);
    assert.match(ui.el("findings").textContent, /<script>/);
    assert.equal(ui.el("briefing-content").children.length, 0);
    assert.equal(ui.el("verdict").textContent, "NO-GO");
    assert.equal(ui.timers.size, 0);
    if (status === "failed") assert.match(ui.el("briefing-quality").textContent, /Draft only/);
  });
}
