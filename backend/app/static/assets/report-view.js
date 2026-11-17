"use strict";

(() => {
  const { setText } = window.MissionControl;
  const checkLabels = { injection: "Prompt injection", missing_details: "Missing details", panic: "Panic / tone" };
  const statuses = {
    processing: "Processing — checks and briefing review are running. Updates are automatic.",
    pending_review: "Ready for Flight Director review — the briefing is awaiting human approval.",
    approved: "Briefing approved by the Flight Director. The launch verdict is unchanged.",
    failed: "Processing failed.",
  };
  function renderList(id, values, emptyText) {
    const list = document.getElementById(id);
    list.replaceChildren();
    for (const value of values.length ? values : [emptyText]) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
  }

  function renderReport(report, expectedId) {
    if (report.id !== expectedId || !Object.hasOwn(statuses, report.status)) {
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
    if (document.getElementById("findings")) renderList("findings", checks.filter((check) => check.outcome !== "clear").map((check) =>
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


  function utcTime(value) {
    if (!value) return "Unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString("en-GB", {
      timeZone: "UTC", dateStyle: "medium", timeStyle: "medium",
    }) + " UTC";
  }

  const money = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 10,
  });
  function usd(value) {
    return value === null || value === undefined ? "Unavailable" : money.format(value) + " USD";
  }

  window.MissionReportView = Object.freeze({ renderReport, utcTime, usd, checkLabels, statuses });
})();
