"use strict";

(() => {
  const { requestJson, setText, validId } = window.MissionControl;
  const { renderReport, utcTime, usd, checkLabels, excerpt, tokens, duration } = window.MissionReportView;
  const match = location.pathname.match(/^\/reports\/([1-9]\d*)\/?$/);
  const selectedId = match && validId(Number(match[1])) ? Number(match[1]) : null;
  let selectedReport = null;
  let detailGeneration = 0;
  let detailController = null;
  let detailTimer = null;
  let detailBusy = false;
  let detailFresh = false;
  let principal = null;
  let credential = "";
  let authBusy = false;
  let authGeneration = 0;
  let authController = null;
  let posting = false;
  let postGeneration = 0;
  let approvalController = null;
  let approvalSignature = null;
  const acknowledgements = new Set();
  const checkboxes = new Map();

  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function list(values) {
    const result = node("ul");
    for (const value of values) result.append(node("li", value));
    return result;
  }

  function renderChecks(report) {
    const focused = document.activeElement?.id;
    const kinds = ["injection", "missing_details", "panic"];
    const expanded = new Set(kinds.filter((kind) => document.getElementById(`findings-${kind}`)?.open));
    const container = document.getElementById("check-cards");
    container.replaceChildren();
    document.getElementById("checks-empty").hidden = report.checks.length > 0;
    checkboxes.clear();
    for (const kind of kinds) {
      const check = report.checks.find((value) => value.check_type === kind);
      const row = node("tr");
      const heading = node("th", checkLabels[kind]);
      heading.setAttribute("scope", "row");
      row.append(heading);
      const state = node("td");
      const unresolved = check?.details_json?.error_reason != null;
      const label = check ? (unresolved ? "Unresolved" : { clear: "Clear", flagged: "Flagged", error: "Unavailable" }[check.outcome]) : (report.status === "processing" ? "Pending" : "Not run");
      const badge = node("span", label);
      badge.className = `status-badge ${unresolved || check?.outcome === "error" ? "danger" : check?.outcome === "flagged" ? "warning" : ""}`;
      state.append(badge);
      row.append(state);
      const findingsCell = node("td");
      if (check) {
        const disclosure = node("details");
        disclosure.id = `findings-${kind}`;
        disclosure.className = "check-findings";
        disclosure.open = expanded.has(kind);
        const summary = node("summary");
        summary.id = `check-summary-${kind}`;
        const findings = check.details_json?.findings ?? [];
        if (check.outcome === "clear") summary.append(node("span", "No concerns · why clear?"));
        else {
          const preview = excerpt(check.reason, 18).text;
          summary.append(node("span", preview.length > 140 ? preview.slice(0, 140) + "…" : preview));
          summary.append(node("small", `${findings.length} finding${findings.length === 1 ? "" : "s"} · View details`));
        }
        disclosure.append(summary);
        disclosure.append(node("p", check.reason));
        if (findings.length) disclosure.append(list(findings));
        if (unresolved) disclosure.append(node("p", `Evaluation unresolved: ${check.details_json.error_reason}. Approval is blocked.`));
        findingsCell.append(disclosure);
      } else findingsCell.append(node("span", "No result recorded"));
      row.append(findingsCell);
      const reviewCell = node("td");
      reviewCell.className = check?.outcome === "flagged" ? "check-review" : "check-no-review";
      if (check?.outcome === "flagged") {
        const label = node("label");
        label.className = "acknowledgement";
        label.hidden = report.status !== "pending_review";
        const checkbox = node("input");
        checkbox.type = "checkbox";
        checkbox.id = `ack-check-${check.id}`;
        checkbox.setAttribute("form", "approval-form");
        checkbox.setAttribute("aria-label", `I acknowledge the ${checkLabels[kind]} flag and all its listed findings`);
        checkbox.checked = acknowledgements.has(check.id);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) acknowledgements.add(check.id);
          else acknowledgements.delete(check.id);
          updateApproval();
        });
        checkboxes.set(check.id, checkbox);
        label.append(checkbox);
        label.append(node("span", "Acknowledge"));
        reviewCell.append(label);
        if (report.status !== "pending_review") {
          const acknowledged = report.approvals.some((audit) => audit.acknowledged_flags_json.includes(check.id));
          reviewCell.append(node("small", acknowledged ? "Reviewed" : "Blocked"));
        }
      } else reviewCell.append(node("span", "—"));
      row.append(reviewCell);
      container.append(row);
    }
    if (focused?.startsWith("check-summary-") || focused?.startsWith("ack-check-")) document.getElementById(focused)?.focus();
  }

  function renderHistory(report) {
    const latest = report.latest_briefing;
    const feedback = latest?.evaluator_feedback;
    const notes = [...(feedback?.feedback ?? []), ...(feedback?.canonical_issues ?? []), ...(feedback?.readability_issues ?? []),
      ...(feedback?.error_reason ? [`Evaluation unavailable: ${feedback.error_reason}`] : [])];
    document.getElementById("latest-feedback").replaceChildren(...list(notes).children);
    document.getElementById("review-notes").hidden = notes.length === 0;
    const previous = report.briefing_versions.filter((version) => version.id !== latest?.id);
    const container = document.getElementById("draft-history");
    container.replaceChildren();
    document.getElementById("older-drafts").hidden = previous.length === 0;
    setText("draft-history-label", `Earlier drafts (${previous.length})`);
    for (const version of previous) {
      const element = node("details");
      element.className = "history-entry";
      element.append(node("summary", `Round ${version.round_number} · ${version.quality_passed ? "passed" : "not passed"}`));
      element.append(node("pre", version.content));
      const notes = version.evaluator_feedback;
      element.append(list([...(notes?.feedback ?? []), ...(notes?.canonical_issues ?? []), ...(notes?.readability_issues ?? []),
        ...(notes?.error_reason ? [`Evaluation unavailable: ${notes.error_reason}`] : [])]));
      container.append(element);
    }
    const audits = document.getElementById("approval-history");
    audits.replaceChildren();
    document.getElementById("audit-section").hidden = report.approvals.length === 0;
    for (const approval of report.approvals) {
      const element = node("div");
      element.append(node("p", `${approval.approved_by} · version #${approval.briefing_version_id}`));
      element.append(node("small", utcTime(approval.approved_at)));
      const details = node("details");
      details.className = "cost-audit";
      details.append(node("summary", "Acknowledgements"));
      details.append(node("p", `Check IDs: ${approval.acknowledged_flags_json.join(", ") || "None"}`));
      element.append(details);
      audits.append(element);
    }
  }

  function renderCosts(report) {
    const container = document.getElementById("cost-summary");
    container.replaceChildren();
    const costs = report.costs;
    const amount = node("strong", costs.cost_complete ? usd(costs.total_cost_usd).replace(" USD", "") : "Unavailable");
    amount.className = "cost-amount";
    container.append(amount);
    const label = node("p", costs.cost_complete ? "Recorded total · USD" : "Usage or pricing is incomplete.");
    label.className = "cost-label";
    container.append(label);
    const counts = node("div");
    counts.className = "cost-tokens";
    for (const [label, value] of [["Input tokens", costs.total_input_tokens], ["Output tokens", costs.total_output_tokens]]) {
      const count = node("div");
      count.append(node("small", label));
      count.append(node("span", tokens(value)));
      counts.append(count);
    }
    container.append(counts);
    const audit = node("details");
    audit.className = "cost-audit";
    audit.append(node("summary", "Cost details"));
    audit.append(node("p", `Known subtotal: ${usd(costs.known_cost_usd)}. This excludes unavailable costs.`));
    if (report.pipeline_runs.length > 1) audit.append(node("p", `Current attempt: ${usd(report.current_run.total_cost_usd)}; known subtotal ${usd(report.current_run.known_cost_usd)}.`));
    container.append(audit);
  }

  function renderTrace(report) {
    const focused = document.activeElement?.id;
    const container = document.getElementById("trace-cards");
    const expanded = new Set();
    report.trace.forEach((step, index) => {
      if (document.getElementById(`trace-${step.id ?? index}`)?.open) expanded.add(step.id ?? index);
    });
    container.replaceChildren();
    setText("trace-count", `${report.trace.length} steps`);
    for (const [index, step] of report.trace.entries()) {
      const details = step.details_json ?? {};
      const roles = { launch_limits: "Launch limits", classification: "Classification", injection: "Hidden instructions", missing_details: "Missing details", panic: "Tone check", draft: "Briefing writer", evaluation: "Independent reviewer", pipeline: "Pipeline" };
      const name = roles[step.step_name] ?? step.step_name.replaceAll("_", " ");
      const card = node("article");
      card.className = "trace-card";
      const disclosure = node("details");
      disclosure.id = `trace-${step.id ?? index}`;
      disclosure.open = expanded.has(step.id ?? index);
      const summary = node("summary");
      summary.id = `trace-summary-${step.id ?? index}`;
      summary.className = "trace-line";
      const title = node("span");
      title.className = "trace-step";
      title.append(node("strong", `${name}${details.round_number ? ` · round ${details.round_number}` : ""}`));
      const state = node("span", { completed: "Done", processing: "Running", failed: "Failed", interrupted: "Interrupted" }[step.status] ?? step.status);
      state.className = `status-badge ${["failed", "interrupted"].includes(step.status) ? "danger" : ""}`;
      title.append(state);
      summary.append(title);
      for (const [value, label, shortLabel] of [[duration(step.latency_ms), "Duration", "Duration"], [`${tokens(step.input_tokens)} / ${tokens(step.output_tokens)}`, "Input / output tokens; — means unreported", "Tokens · in / out"], [step.cost_usd == null ? "—" : usd(step.cost_usd).replace(" USD", ""), "Cost in USD; — means unavailable", "Cost · USD"]]) {
        const metric = node("span");
        metric.className = "trace-metric";
        metric.title = label;
        metric.setAttribute("aria-label", `${label}: ${value}`);
        const caption = node("small", shortLabel);
        caption.className = "metric-label";
        metric.append(caption);
        metric.append(node("span", value));
        summary.append(metric);
      }
      disclosure.append(summary);
      const inspection = node("div");
      inspection.className = "trace-extra";
      inspection.append(node("p", `Started ${utcTime(step.started_at)} · Finished ${utcTime(step.completed_at)}`));
      inspection.append(node("p", details.model ? `Model: ${details.model}${details.provider ? ` · ${details.provider}` : ""}` : "No model call recorded."));
      inspection.append(node("pre", JSON.stringify(details, null, 2)));
      disclosure.append(inspection);
      card.append(disclosure);
      container.append(card);
    }
    if (focused?.startsWith("trace-summary-")) document.getElementById(focused)?.focus();
  }

  async function loadDetail(generation) {
    detailFresh = false;
    detailBusy = true;
    updateApproval();
    document.getElementById("refresh-report").disabled = true;
    document.getElementById("poll-error").hidden = true;
    detailController = new AbortController();
    try {
      const report = await requestJson(`/api/reports/${selectedId}`, { signal: detailController.signal });
      if (generation !== detailGeneration) return;
      renderReport(report, selectedId);
      const signature = JSON.stringify([report.current_run?.id, report.latest_briefing, report.checks, report.status]);
      if (signature !== approvalSignature) acknowledgements.clear();
      approvalSignature = signature;
      selectedReport = report;
      detailFresh = true;
      renderChecks(report);
      renderHistory(report);
      renderCosts(report);
      renderTrace(report);
      if (report.status === "processing") detailTimer = setTimeout(() => loadDetail(generation), 2000);
    } catch (error) {
      if (generation !== detailGeneration) return;
      detailFresh = false;
      if (error.status === 404) setText("status-message", "Report not found.");
      setText("poll-error-message", error.status === 404 ? "This report could not be found. Return to Reports or start a new report." : "Status updates paused. Your saved report is unchanged. Retry to check its status.");
      document.getElementById("retry-status").hidden = error.status === 404;
      document.getElementById("poll-error").hidden = false;
    } finally {
      if (generation === detailGeneration) {
        detailBusy = false;
        updateApproval();
      }
    }
  }

  function refreshDetail() {
    if (selectedId === null || posting) return;
    detailController?.abort();
    clearTimeout(detailTimer);
    return loadDetail(++detailGeneration);
  }

  function eligibilityReason(report) {
    if (!report) return "Select a report before approval.";
    if (report.status === "approved") return "This briefing has already been approved.";
    if (report.status === "processing") return "Processing must finish before approval.";
    if (report.status === "failed") return "Processing failed. This briefing cannot be approved.";
    const run = report.current_run;
    const latest = report.latest_briefing;
    const checks = report.checks;
    if (report.status !== "pending_review" || run?.status !== "pending_review" || !run.completed_at || run.failure_reason != null
      || !validId(run.id) || !latest?.quality_passed || latest.evaluator_feedback == null
      || !validId(latest.id) || latest.run_id !== run.id || latest.report_id !== report.id
      || report.briefing_versions.at(-1)?.id !== latest.id
      || checks.length !== 3 || new Set(checks.map((check) => check.check_type)).size !== 3
      || !["injection", "missing_details", "panic"].every((kind) => checks.some((check) => check.check_type === kind))
      || checks.some((check) => check.run_id !== run.id || !["clear", "flagged"].includes(check.outcome) || check.details_json?.error_reason != null)
      || report.trace.some((step) => step.status === "processing")
      || report.approvals.some((audit) => audit.briefing_version_id === latest.id)) {
      return "The latest briefing and every required check must complete successfully before approval.";
    }
    return null;
  }

  function updateApproval() {
    const reason = eligibilityReason(selectedReport);
    const allowed = !reason && detailFresh && principal?.role === "flight_director" && !posting;
    for (const [id, checkbox] of checkboxes) {
      checkbox.disabled = !allowed;
      checkbox.checked = acknowledgements.has(id);
    }
    const flags = selectedReport?.checks.filter((check) => check.outcome === "flagged") ?? [];
    const acknowledged = flags.every((check) => acknowledgements.has(check.id));
    document.getElementById("approve-briefing").disabled = !allowed || !acknowledged;
    let message = reason;
    if (posting) message = "Saving human approval…";
    else if (!detailFresh && selectedReport) message = "Refresh this report before approval.";
    else if (!reason && principal?.role !== "flight_director") message = "Sign in as Flight Director to approve.";
    else if (!reason && !acknowledged) message = "Review and acknowledge the flagged checks.";
    else if (!reason) message = "Ready for your approval.";
    setText("approval-status", message);
    document.getElementById("approval-panel").hidden = selectedReport?.status !== "pending_review" && !principal && !authBusy;
    document.getElementById("approval-form").hidden = selectedReport?.status !== "pending_review";
    document.getElementById("director-access").hidden = selectedReport?.status !== "pending_review" && !principal && !authBusy;
    document.getElementById("director-token").disabled = posting || authBusy;
    document.getElementById("connect-director").disabled = posting || authBusy;
    document.getElementById("disconnect-director").disabled = posting || (!principal && !authBusy);
    document.getElementById("refresh-report").disabled = posting || detailBusy || selectedId === null;
  }

  function clearIdentity(message = "Signed out. Sign in to approve a briefing.") {
    credential = "";
    principal = null;
    document.getElementById("director-token").value = "";
    acknowledgements.clear();
    setText("auth-status", message);
    updateApproval();
  }

  async function connect(event) {
    event.preventDefault();
    if (posting || authBusy) return;
    const token = document.getElementById("director-token").value;
    const generation = ++authGeneration;
    authController?.abort();
    clearIdentity("Checking access…");
    if (!/^[\x21-\x7E]{32,256}$/.test(token)) {
      setText("auth-status", "Enter your configured approval access key (32–256 characters, no spaces).");
      return;
    }
    authBusy = true;
    updateApproval();
    authController = new AbortController();
    try {
      const identity = await requestJson("/api/auth/me", {
        signal: authController.signal, headers: { Authorization: `Bearer ${token}` },
      });
      if (generation !== authGeneration) return;
      if (typeof identity.subject !== "string" || !["flight_director", "engineer"].includes(identity.role)) throw new Error("Invalid identity");
      principal = identity;
      credential = token;
      setText("auth-status", identity.role === "flight_director" ? "Signed in as Flight Director." : "Signed in as Engineer. Flight Director access is required to approve.");
    } catch {
      if (generation === authGeneration) clearIdentity("Could not verify the access key. Check it and try again.");
    } finally {
      if (generation === authGeneration) { authBusy = false; updateApproval(); }
    }
  }

  function notice(message) {
    setText("approval-message", message);
    document.getElementById("approval-message").hidden = false;
  }

  async function approve(event) {
    event.preventDefault();
    if (posting || document.getElementById("approve-briefing").disabled) return;
    const generation = ++postGeneration;
    const target = selectedReport;
    const payload = {
      run_id: target.current_run.id,
      briefing_version_id: target.latest_briefing.id,
      acknowledged_check_result_ids: [...acknowledgements].sort((a, b) => a - b),
    };
    posting = true;
    detailFresh = false;
    document.getElementById("approval-message").hidden = true;
    updateApproval();
    approvalController = new AbortController();
    try {
      const result = await requestJson(`/api/reports/${target.id}/approve`, {
        signal: approvalController.signal, method: "POST",
        headers: { Authorization: `Bearer ${credential}`, "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (generation !== postGeneration) return;
      if (result.status !== "approved" || result.report_id !== target.id || result.run_id !== payload.run_id
        || result.approval?.briefing_version_id !== payload.briefing_version_id) throw new Error("Unconfirmed approval");
      notice(`Briefing version #${payload.briefing_version_id} approved. The launch verdict is unchanged.`);
    } catch (error) {
      if (generation !== postGeneration) return;
      if ([401, 403].includes(error.status)) clearIdentity("Your approval access has expired. Sign in again.");
      notice(error.status === 409
        ? "The report changed or an approval condition was not met. Review the refreshed report and acknowledgements."
        : "Approval could not be confirmed. Review the refreshed report before trying again; it may already be approved.");
    } finally {
      if (generation === postGeneration) {
        posting = false;
        acknowledgements.clear();
        updateApproval();
        await refreshDetail();  // Safe GET only; never retry a POST automatically.
      }
    }
  }

  document.getElementById("refresh-report").addEventListener("click", refreshDetail);
  document.getElementById("retry-status").addEventListener("click", refreshDetail);
  document.getElementById("director-auth").addEventListener("submit", connect);
  document.getElementById("approval-form").addEventListener("submit", approve);
  document.getElementById("disconnect-director").addEventListener("click", () => {
    if (posting) return;
    authGeneration += 1;
    authController?.abort();
    authBusy = false;
    clearIdentity();
  });
  window.addEventListener("pagehide", () => {
    detailGeneration += 1;
    detailController?.abort();
    clearTimeout(detailTimer);
    authGeneration += 1;
    postGeneration += 1;
    authController?.abort();
    approvalController?.abort();
    if (posting) notice("The approval response was interrupted. Refresh report status before trying again.");
    posting = false;
    authBusy = false;
    detailBusy = false;
    detailFresh = false;
    clearIdentity();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) refreshDetail();
  });
  updateApproval();
  if (selectedId === null) {
    setText("status-message", "Invalid report link.");
    setText("poll-error-message", "This report link is invalid. Return to Reports or start a new report.");
    document.getElementById("retry-status").hidden = true;
    document.getElementById("poll-error").hidden = false;
  } else {
    document.title = `Report #${selectedId} · Mission Control`;
    setText("report-heading", `Report #${selectedId}`);
    document.getElementById("report-link").href = `/reports/${selectedId}`;
    refreshDetail();
  }
})();
