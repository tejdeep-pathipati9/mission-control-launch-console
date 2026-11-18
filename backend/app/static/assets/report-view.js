"use strict";

(() => {
  const { setText } = window.MissionControl;
  const checkLabels = { injection: "Hidden instructions", missing_details: "Missing details", panic: "Panic / tone" };
  const statuses = { processing: "Processing", pending_review: "Ready for review", approved: "Approved", failed: "Processing failed" };
  const toneForStatus = (status) => status === "failed" ? "danger" : status === "pending_review" ? "warning" : "";

  function renderList(id, values) {
    const list = document.getElementById(id);
    list.replaceChildren();
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
  }

  function excerpt(value, limit = 80) {
    const words = String(value ?? "").trim().split(/\s+/);
    return { text: words.slice(0, limit).join(" ") + (words.length > limit ? "…" : ""), shortened: words.length > limit };
  }

  function reportOverview(report) {
    const verdict = report.verdict === "go" ? "Readings meet launch limits (GO)."
      : report.verdict === "no_go" ? "Readings breach launch limits (NO-GO)." : "Launch verdict is unavailable.";
    const checks = report.checks ?? [];
    if (report.status === "processing") return `${verdict} Processing is still running. Wait for the reviewed briefing.`;
    if (checks.length !== 3 || checks.some((check) => !["clear", "flagged"].includes(check.outcome) || check.details_json?.error_reason != null)) {
      return `${verdict} Required checks are incomplete. Resolve them before briefing approval.`;
    }
    const flagged = checks.filter((check) => check.outcome === "flagged");
    if (flagged.length === 1 && flagged[0].check_type === "missing_details") {
      return `${verdict} Missing details remain flagged for review. Ask the reporting engineer to fill the gaps listed in the checks.`;
    }
    if (flagged.length) {
      const names = { injection: "hidden instructions", missing_details: "missing details", panic: "tone concerns" };
      const labels = flagged.map((check) => names[check.check_type] ?? "a concern");
      const concerns = labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}`;
      return `${verdict} Checks flagged ${concerns}. Review those findings and follow up with the reporting engineer.`;
    }
    return `${verdict} No checks are flagged. Review the saved briefing before approval.`;
  }

  function briefingText(report, version) {
    // Draft traces already retain validated structured output. Use only the exact version's
    // successful draft; never mix attempts or parse model-authored headings as boundaries.
    const draft = (report.trace ?? []).findLast((step) => {
      const summary = step.details_json?.validated_output?.summary;
      return step.step_name === "draft" && step.status === "completed"
        && step.run_id === version.run_id && step.details_json?.round_number === version.round_number
        && typeof summary === "string" && version.content.startsWith(`Anomaly summary: ${summary}\n`);
    });
    const summary = draft?.details_json.validated_output.summary;
    const text = summary ?? version.content;
    const padded = String(text).trim().split(/\s+/).length > 80
      || /\b(canonical (verdict|facts|values)|free[- ]text note field|automated screening returned)\b/i.test(text);
    // This overview is explicitly labelled as application-derived, never a rewritten model draft.
    // The complete saved briefing and the exact version approved remain unchanged.
    return padded ? { text: reportOverview(report), overview: true, shortened: false }
      : { ...excerpt(text), overview: false };
  }

  function renderReport(report, expectedId) {
    if (report.id !== expectedId || !Object.hasOwn(statuses, report.status)) throw new Error("Unexpected report status.");
    for (const name of ["engineer_name", "station", "wind_knots", "temp_c", "fuel_pct", "note"]) {
      setText(`original-${name}`, String(report[name] ?? "Unavailable"));
    }
    setText("report-meta", `${report.station} · ${report.engineer_name}`);
    setText("status-message", statuses[report.status]);
    document.getElementById("status-message").className = `status-badge ${toneForStatus(report.status)}`;
    const failure = report.status === "failed" ? report.current_run?.failure_message : null;
    setText("failure-detail", failure ?? "");
    document.getElementById("failure-detail").hidden = !failure;
    setText("verdict", report.verdict === "go" ? "GO" : "NO-GO");
    document.getElementById("verdict").className = `verdict-badge ${report.verdict === "no_go" ? "danger" : ""}`;
    const unknown = report.status === "processing" ? "Pending" : "Unavailable";
    setText("severity", report.severity ? report.severity.toUpperCase() : unknown);
    document.getElementById("severity").className = `status-badge ${report.severity === "high" ? "danger" : report.severity === "medium" ? "warning" : ""}`;
    const subsystem = report.subsystem?.replaceAll("_", " ");
    setText("subsystem", subsystem ? subsystem[0].toUpperCase() + subsystem.slice(1) : unknown);
    const time = document.getElementById("brief-by");
    if (report.brief_by_at) {
      time.dateTime = report.brief_by_at;
      time.textContent = new Date(report.brief_by_at).toLocaleString("en-GB", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" }) + " UTC";
    } else {
      time.removeAttribute("datetime");
      time.textContent = unknown;
    }
    renderList("violations", report.violations_json.map((value) => value
      .replaceAll("wind_knots", "Wind (kn)").replaceAll("temp_c", "Temperature (°C)")
      .replaceAll("fuel_pct", "Fuel (%)").replaceAll("<=", "≤").replaceAll(">=", "≥")));
    document.getElementById("violations-panel").hidden = report.violations_json.length === 0;
    for (const field of ["wind_knots", "temp_c", "fuel_pct"]) {
      document.getElementById(`reading-${field}`).className = report.violations_json.some((value) => value.startsWith(field)) ? "reading-breach" : "";
    }
    const checks = report.checks ?? [];
    const complete = checks.filter((check) => ["clear", "flagged"].includes(check.outcome) && check.details_json?.error_reason == null).length;
    const errors = checks.length - complete;
    setText("checks-summary", `${complete} of 3 checks completed${errors ? `; ${errors} could not complete` : ""}.`);
    const version = report.latest_briefing;
    document.getElementById("briefing").hidden = !version;
    document.getElementById("briefing-pending").hidden = Boolean(version);
    setText("briefing-pending-message", report.status === "processing" ? "The pipeline is preparing this briefing." : "No reviewed briefing is available for this report.");
    if (version) {
      const summary = briefingText(report, version);
      setText("briefing-heading", summary.overview ? "Report overview" : "Flight Director briefing");
      setText("briefing-quality", version.quality_passed ? (summary.overview ? "Saved review passed" : "Quality passed") : "Draft · not passed");
      document.getElementById("briefing-quality").className = `status-badge ${version.quality_passed ? "" : "warning"}`;
      setText("briefing-content", summary.text);
      setText("briefing-excerpt", summary.overview ? "From saved readings and checks · full original briefing below." : "Excerpt · full saved briefing below.");
      document.getElementById("briefing-excerpt").hidden = !summary.overview && !summary.shortened;
      setText("briefing-full-content", version.content);
    }
    document.getElementById("report-result").hidden = false;
  }

  function utcTime(value) {
    if (!value) return "Unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString("en-GB", { timeZone: "UTC", dateStyle: "medium", timeStyle: "medium" }) + " UTC";
  }
  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 10 });
  const usd = (value) => value == null ? "Unavailable" : money.format(value) + " USD";
  const tokens = (value) => value == null ? "—" : new Intl.NumberFormat("en-US").format(value);
  const duration = (value) => value == null ? "—" : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`;
  window.MissionReportView = Object.freeze({ renderReport, utcTime, usd, checkLabels, statuses, toneForStatus, excerpt, briefingText, tokens, duration });
})();
