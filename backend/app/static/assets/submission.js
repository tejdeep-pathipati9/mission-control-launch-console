"use strict";

const form = document.querySelector("#report-form");
const fields = document.querySelector("#report-fields");
const submitButton = document.querySelector("#submit-report");
const statusSection = document.querySelector("#report-status");
const errorSection = document.querySelector("#form-errors");
const pollError = document.querySelector("#poll-error");
const textFields = { engineer_name: 120, station: 120, note: 10000 };
const numberFields = ["wind_knots", "temp_c", "fuel_pct"];
const labels = {
  engineer_name: "Engineer name", station: "Station", note: "Anomaly note",
  wind_knots: "Wind", temp_c: "Temperature", fuel_pct: "Fuel",
};
const checkLabels = { injection: "Prompt injection", missing_details: "Missing details", panic: "Panic / tone" };
const statuses = {
  processing: "Processing — checks and briefing review are running. Updates are automatic.",
  pending_review: "Ready for Flight Director review — the briefing is awaiting human approval.",
  approved: "Briefing approved by the Flight Director. The launch verdict is unchanged.",
  failed: "Processing failed.",
};
let generation = 0;
let pollTimer = null;
let requestController = null;
let reportId = null;

function setText(id, value) {
  const element = document.getElementById(id);
  if (element.textContent !== value) element.textContent = value;
}

function renderList(id, values, emptyText) {
  const list = document.getElementById(id);
  list.replaceChildren();
  for (const value of values.length ? values : [emptyText]) {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  }
}

function clearErrors() {
  errorSection.hidden = true;
  document.querySelector("#error-list").replaceChildren();
  for (const name of Object.keys(labels)) {
    document.getElementById(name).removeAttribute("aria-invalid");
    document.getElementById(`${name}-error`).hidden = true;
  }
}

function showErrors(errors) {
  clearErrors();
  const list = document.querySelector("#error-list");
  for (const { field, message } of errors) {
    const item = document.createElement("li");
    if (Object.hasOwn(labels, field)) {
      const input = document.getElementById(field);
      input.setAttribute("aria-invalid", "true");
      setText(`${field}-error`, message);
      document.getElementById(`${field}-error`).hidden = false;
      const link = document.createElement("a");
      link.href = `#${field}`;
      link.textContent = `${labels[field]}: ${message}`;
      item.append(link);
    } else {
      item.textContent = message;
    }
    list.append(item);
  }
  errorSection.hidden = false;
  errorSection.focus();
}

function collectReport() {
  const payload = {};
  const errors = [];
  for (const [name, maxLength] of Object.entries(textFields)) {
    const value = document.getElementById(name).value.trim();
    if (!value) errors.push({ field: name, message: "Enter a value." });
    else if (value.length > maxLength) errors.push({ field: name, message: `Use ${maxLength} characters or fewer.` });
    payload[name] = value;
  }
  for (const name of numberFields) {
    const input = document.getElementById(name);
    const value = Number(input.value);
    if (!input.value.trim() || !Number.isFinite(value) || input.validity.badInput) {
      errors.push({ field: name, message: "Enter a finite number." });
    } else if (input.min !== "" && value < Number(input.min)) {
      errors.push({ field: name, message: `Enter a value of ${input.min} or higher.` });
    }
    payload[name] = value;
  }
  return { payload, errors };
}

function stopRequests() {
  generation += 1;
  clearTimeout(pollTimer);
  pollTimer = null;
  requestController?.abort();
  requestController = null;
}

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  requestController = controller;
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {
      ...options, signal: controller.signal, cache: "no-store",
      headers: { Accept: "application/json", ...options.headers },
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error("The server returned an unreadable response."); }
    if (!response.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
      error.status = response.status;
      error.details = data.detail;
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timeout);
    if (requestController === controller) requestController = null;
  }
}

function validId(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function reportUrl(id) {
  return `/?report=${id}#report-status`;
}

function activateReport(id, confirmed = false) {
  reportId = id;
  fields.disabled = true;
  statusSection.hidden = false;
  document.querySelector("#report-result").hidden = true;
  pollError.hidden = true;
  setText("report-heading", `Report #${id}`);
  setText("status-message", confirmed ? "Report saved. Loading its processing status…" : "Loading report status…");
  document.querySelector("#report-link").href = reportUrl(id);
  history.replaceState(null, "", reportUrl(id));
}

function renderReport(report) {
  if (report.id !== reportId || !Object.hasOwn(statuses, report.status)) {
    throw new Error("The server returned an unexpected report status.");
  }
  for (const name of Object.keys(labels)) {
    document.getElementById(name).value = report[name] ?? "";
  }
  const failure = report.status === "failed" ? report.current_run?.failure_message : null;
  setText("status-message", `${statuses[report.status]}${failure ? ` ${failure}` : ""}`);
  setText("verdict", { go: "GO", no_go: "NO-GO" }[report.verdict] ?? "Unavailable");
  setText("severity", report.severity ? report.severity.toUpperCase() : "Awaiting classification");
  setText("subsystem", report.subsystem ? report.subsystem.replaceAll("_", " ") : "Awaiting classification");
  const time = document.querySelector("#brief-by");
  if (report.brief_by_at) {
    time.dateTime = report.brief_by_at;
    time.textContent = new Date(report.brief_by_at).toLocaleString("en-GB", {
      timeZone: "UTC", dateStyle: "medium", timeStyle: "medium",
    }) + " UTC";
  } else {
    time.removeAttribute("datetime");
    time.textContent = "Awaiting classification";
  }
  renderList("violations", report.violations_json, "None. All readings are within launch limits.");
  const checks = report.checks ?? [];
  const complete = checks.filter((check) =>
    ["clear", "flagged"].includes(check.outcome) && check.details_json?.error_reason == null
  ).length;
  const errors = checks.length - complete;
  setText("checks-summary", `${complete} of 3 checks completed${errors ? `; ${errors} could not complete` : ""}.`);
  renderList("findings", checks.filter((check) => check.outcome !== "clear").map((check) =>
    `${checkLabels[check.check_type] ?? check.check_type} — ${check.outcome === "error" ? "unavailable" : "flagged"}: ${check.reason}`
  ), report.status === "processing" ? "No findings recorded yet." : "No flagged findings.");
  const version = report.latest_briefing;
  document.querySelector("#briefing").hidden = !version;
  if (version) {
    setText("briefing-quality", version.quality_passed
      ? (report.status === "approved" ? "Quality review passed. Briefing approved." : "Quality review passed. Human approval required.")
      : "Draft only — quality review has not passed.");
    setText("briefing-content", version.content);
  }
  document.querySelector("#report-result").hidden = false;
}

async function pollReport(attempt) {
  if (attempt !== generation || reportId === null) return;
  pollError.hidden = true;
  document.querySelector("#retry-status").disabled = true;
  try {
    const report = await requestJson(`/api/reports/${reportId}`);
    if (attempt !== generation) return;
    renderReport(report);
    if (report.status === "processing") {
      pollTimer = setTimeout(() => pollReport(attempt), 2000);
    }
  } catch (error) {
    if (attempt !== generation) return;
    setText("poll-error-message", error.status === 404
      ? "This report could not be found. Check the report link or start a new report."
      : "Status updates paused. Your saved report is unchanged. Retry to check its status.");
    document.querySelector("#retry-status").hidden = error.status === 404;
    pollError.hidden = false;
  } finally {
    if (attempt === generation) document.querySelector("#retry-status").disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (fields.disabled) return;
  clearErrors();
  const { payload, errors } = collectReport();
  if (errors.length) { showErrors(errors); return; }
  stopRequests();
  const attempt = generation;
  fields.disabled = true;
  submitButton.textContent = "Submitting…";
  form.setAttribute("aria-busy", "true");
  try {
    const accepted = await requestJson("/api/reports", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (attempt !== generation) return;
    if (!validId(accepted.report_id)) throw new Error("Submission could not be confirmed.");
    activateReport(accepted.report_id, true);
    document.querySelector("#report-heading").focus();
    await pollReport(attempt);
  } catch (error) {
    if (attempt !== generation) return;
    fields.disabled = false;
    if (error.status === 422 && Array.isArray(error.details)) {
      showErrors(error.details.map((item) => ({ field: item.loc?.[1], message: item.msg })));
    } else {
      showErrors([{ message: error.status ? error.message
        : "Submission could not be confirmed. The report may have been saved; avoid submitting it again until confirmed." }]);
    }
  } finally {
    if (attempt === generation) {
      submitButton.textContent = "Submit report";
      form.removeAttribute("aria-busy");
    }
  }
});

document.querySelector("#retry-status").addEventListener("click", () => {
  clearTimeout(pollTimer);
  pollReport(generation);
});

document.querySelector("#new-report").addEventListener("click", () => {
  stopRequests();
  reportId = null;
  form.reset();
  clearErrors();
  statusSection.hidden = true;
  fields.disabled = false;
  form.removeAttribute("aria-busy");
  submitButton.textContent = "Submit report";
  history.replaceState(null, "", "/");
  document.getElementById("engineer_name").focus();
});

window.addEventListener("pagehide", stopRequests);
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  if (reportId !== null) pollReport(generation);
  else if (fields.disabled) {
    fields.disabled = false;
    submitButton.textContent = "Submit report";
    form.removeAttribute("aria-busy");
    showErrors([{ message: "Submission was interrupted and may have been saved. Confirm before submitting again." }]);
  }
});

submitButton.disabled = false;
const savedId = new URLSearchParams(location.search).get("report");
if (savedId !== null) {
  if (/^[1-9]\d*$/.test(savedId) && validId(Number(savedId))) {
    activateReport(Number(savedId));
    pollReport(generation);
  } else {
    showErrors([{ message: "This report link is invalid. Submit a new report or use a valid report link." }]);
  }
}
