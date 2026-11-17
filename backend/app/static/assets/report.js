"use strict";

(() => {
  const { setText, requestJson, validId } = window.MissionControl;
  const pollError = document.querySelector("#poll-error");
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
  const match = location.pathname.match(/^\/reports\/([1-9]\d*)\/?$/);
  const reportId = match && validId(Number(match[1])) ? Number(match[1]) : null;

  function renderList(id, values, emptyText) {
    const list = document.getElementById(id);
    list.replaceChildren();
    for (const value of values.length ? values : [emptyText]) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
  }

  function stopRequests() {
    generation += 1;
    clearTimeout(pollTimer);
    pollTimer = null;
    requestController?.abort();
    requestController = null;
  }

  function renderReport(report) {
    if (report.id !== reportId || !Object.hasOwn(statuses, report.status)) {
      throw new Error("The server returned an unexpected report status.");
    }
    for (const name of ["engineer_name", "station", "wind_knots", "temp_c", "fuel_pct", "note"]) {
      setText(`original-${name}`, String(report[name] ?? "Unavailable"));
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
      requestController = new AbortController();
      const report = await requestJson(`/api/reports/${reportId}`, { signal: requestController.signal });
      if (attempt !== generation) return;
      renderReport(report);
      if (report.status === "processing") {
        pollTimer = setTimeout(() => pollReport(attempt), 2000);
      }
    } catch (error) {
      if (attempt !== generation) return;
      if (error.status === 404) setText("status-message", "Report not found.");
      setText("poll-error-message", error.status === 404
        ? "This report could not be found. Check the report link or start a new report."
        : "Status updates paused. Your saved report is unchanged. Retry to check its status.");
      document.querySelector("#retry-status").hidden = error.status === 404;
      pollError.hidden = false;
    } finally {
      if (attempt === generation) document.querySelector("#retry-status").disabled = false;
    }
  }

  document.querySelector("#retry-status").addEventListener("click", () => {
    clearTimeout(pollTimer);
    pollReport(generation);
  });
  window.addEventListener("pagehide", stopRequests);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && reportId !== null) pollReport(generation);
  });
  if (reportId === null) {
    setText("status-message", "Invalid report link.");
    setText("poll-error-message", "This report link is invalid. Use a valid report link or start a new report.");
    document.querySelector("#retry-status").hidden = true;
    pollError.hidden = false;
  } else {
    document.title = `Report #${reportId} · Mission Control`;
    setText("report-heading", `Report #${reportId}`);
    document.querySelector("#report-link").href = `/reports/${reportId}`;
    pollReport(generation);
  }
})();
