const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = process.env.UI_STATIC_DIR || path.resolve(__dirname, "../../app/static");
const names = ["engineer_name", "station", "wind_knots", "temp_c", "fuel_pct", "note"];
const sample = { engineer_name: "Avery Chen", station: "Weather", wind_knots: "26", temp_c: "4", fuel_pct: "98", note: "Backup sensor agrees." };

// In-memory DOM surface for controller tests. This is not a visual browser test.
function harness(responses = [], pathname = "/") {
  const url = new URL(pathname, "http://local.test");
  const page = url.pathname === "/" ? "index.html" : (url.pathname === "/reports" ? "queue.html" : "report.html");
  const html = fs.readFileSync(path.join(root, page), "utf8");
  const navigations = [];
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
      this.href = attrs.match(/\bhref="([^"]+)"/)?.[1] ?? "";
      this.hidden = /\bhidden\b/.test(attrs);
      this.disabled = /\bdisabled\b/.test(attrs);
      this.validity = { badInput: false };
      this.min = attrs.match(/\bmin="([^"]+)"/)?.[1] ?? "";
      this.attributes = {};
      this.children = [];
      this.events = {};
      this.text = "";
    }
    set id(value) { this.identifier = value; if (value) elements.set(value, this); }
    get id() { return this.identifier; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map((child) => child.textContent).join(" "); }
    set innerHTML(value) { throw new Error("Untrusted HTML must never be rendered"); }
    append(child) { this.children.push(child); }
    replaceChildren(...children) { this.text = ""; this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    get checked() { return this.isChecked || false; }
    set checked(value) { this.isChecked = value; }
    addEventListener(name, callback) { this.events[name] = callback; }
    focus() { focused = this.id; }
    reset() { for (const name of names) elements.get(name).value = ""; }
  }
  for (const match of html.matchAll(/<[a-z][a-z0-9]*\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    elements.set(match[2], new Element(match[2], match[1]));
  }
  const location = { href: url.href, pathname: url.pathname, search: url.search, assign: (url) => { navigations.push(url); location.href = url; } };
  const context = {
    document: {
      querySelector: (selector) => {
        const result = elements.get(selector.slice(1));
        assert.ok(result, `Unknown element ${selector}`);
        return result;
      },
      getElementById: (id) => elements.get(id),
      createElement: () => new Element(),
      get activeElement() { return elements.get(focused); },
    },
    window: { addEventListener: (name, callback) => { windowEvents[name] = callback; } },
    location,
    history: { replaceState: (_state, _title, target) => {
      location.href = target;
      const updated = new URL(target, "http://local.test");
      location.pathname = updated.pathname; location.search = updated.search;
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
  vm.createContext(context);
  for (const match of html.matchAll(/<script src="\/assets\/([^"]+)" defer><\/script>/g)) {
    vm.runInContext(fs.readFileSync(path.join(root, "assets", match[1].split("?")[0]), "utf8"), context);
  }
  return {
    el: (id) => elements.get(id), requests, timers, location, windowEvents, navigations,
    focused: () => focused,
    fill: (values = sample) => { for (const [key, value] of Object.entries(values)) elements.get(key).value = value; },
    submit: () => elements.get("report-form").events.submit({ preventDefault() {} }),
    click: (id) => elements.get(id).events.click({ preventDefault() {} }),
    change: (id, checked) => { const element = elements.get(id); element.checked = checked; element.events.change?.(); },
    submitForm: (id) => elements.get(id).events.submit({ preventDefault() {} }),
    poll: async (delay = 2000) => {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, "No scheduled poll");
      timers.delete(entry[0]);
      await entry[1].callback();
    },
  };
}

module.exports = { harness, sample };
