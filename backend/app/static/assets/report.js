"use strict";

(() => {
  const { requestJson, setText, validId } = window.MissionControl;
  const { renderReport, utcTime, usd, checkLabels } = window.MissionReportView;
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

  function card(title) {
    const element = node("article");
    element.className = "detail-card";
    element.append(node("h3", title));
    return element;
  }

  function renderChecks(report) {
    const container = document.getElementById("check-cards");
    container.replaceChildren();
    if (!report.checks.length) container.append(node("p", "No checks were recorded for this run."));
    checkboxes.clear();
    for (const check of report.checks) {
      const element = card(`${checkLabels[check.check_type] ?? check.check_type} — ${check.outcome}`);
      element.append(node("p", check.reason));
      element.append(list(check.details_json?.findings ?? []));
      if (check.details_json?.error_reason) element.append(node("p", `Evaluation unresolved: ${check.details_json.error_reason}`));
      if (check.outcome === "flagged") {
        const label = node("label");
        label.className = "acknowledgement";
        const checkbox = node("input");
        checkbox.type = "checkbox";
        checkbox.id = `ack-check-${check.id}`;
        checkbox.setAttribute("form", "approval-form");
        checkbox.checked = acknowledgements.has(check.id);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) acknowledgements.add(check.id);
          else acknowledgements.delete(check.id);
          updateApproval();
        });
        checkboxes.set(check.id, checkbox);
        label.append(checkbox);
        label.append(node("span", `I acknowledge this ${checkLabels[check.check_type] ?? check.check_type} flag and all its listed findings.`));
        element.append(label);
      }
      container.append(element);
    }
  }

  function renderHistory(report) {
    const latest = report.latest_briefing;
    const feedback = latest?.evaluator_feedback;
    document.getElementById("latest-feedback").replaceChildren(...list([
      ...(feedback?.feedback ?? []), ...(feedback?.canonical_issues ?? []),
      ...(feedback?.error_reason ? [`Evaluation unavailable: ${feedback.error_reason}`] : []),
    ]).children);
    const previous = report.briefing_versions.filter((version) => version.id !== latest?.id);
    const container = document.getElementById("draft-history");
    container.replaceChildren();
    document.getElementById("older-drafts").hidden = previous.length === 0;
    setText("draft-history-label", `Earlier drafts (${previous.length})`);
    for (const version of previous) {
      const element = card(`Draft ${version.round_number} — ${version.quality_passed ? "quality passed" : "quality not passed"}`);
      element.append(node("pre", version.content));
      const notes = version.evaluator_feedback;
      element.append(list([...(notes?.feedback ?? []), ...(notes?.canonical_issues ?? []),
        ...(notes?.error_reason ? [`Evaluation unavailable: ${notes.error_reason}`] : [])]));
      container.append(element);
    }
    const audits = document.getElementById("approval-history");
    audits.replaceChildren();
    if (!report.approvals.length) audits.append(node("p", "No human approval has been recorded."));
    for (const approval of report.approvals) {
      const element = card(`Briefing version #${approval.briefing_version_id}`);
      element.append(node("p", `Approved by ${approval.approved_by} at ${utcTime(approval.approved_at)}.`));
      element.append(node("p", `Acknowledged check IDs: ${approval.acknowledged_flags_json.join(", ") || "None"}.`));
      audits.append(element);
    }
  }

  function renderCosts(report) {
    const container = document.getElementById("cost-summary");
    container.replaceChildren();
    const summaries = [["Report cost", report.costs]];
    if (report.pipeline_runs.length > 1) summaries.push(["Current attempt", report.current_run]);
    for (const [title, costs] of summaries) {
      const element = card(title);
      element.append(node("p", `${costs.cost_complete ? "Total" : "Recorded total (incomplete or provisional)"}: ${usd(costs.total_cost_usd)}.`));
      element.append(node("p", `Known subtotal: ${usd(costs.known_cost_usd)}. This subtotal excludes unavailable costs.`));
      element.append(node("p", `Input tokens: ${costs.total_input_tokens ?? "Unavailable"}; output tokens: ${costs.total_output_tokens ?? "Unavailable"}.`));
      container.append(element);
    }
  }

  function renderTrace(report) {
    const container = document.getElementById("trace-cards");
    container.replaceChildren();
    for (const step of report.trace) {
      const details = step.details_json;
      const roles = { draft: "Briefing writer", evaluation: "Independent reviewer" };
      const name = roles[step.step_name] ?? step.step_name.replaceAll("_", " ");
      const element = card(`${name}${details.round_number ? ` — round ${details.round_number}` : ""}: ${step.status}`);
      element.append(node("p", `Started: ${utcTime(step.started_at)}. Completed: ${utcTime(step.completed_at)}.`));
      element.append(node("p", `Latency: ${step.latency_ms === null ? "Unavailable" : step.latency_ms + " ms"}.`));
      element.append(node("p", `Input tokens: ${step.input_tokens ?? "Unavailable"}; output tokens: ${step.output_tokens ?? "Unavailable"}; cost: ${usd(step.cost_usd)}.`));
      element.append(node("p", `Model: ${details.model ?? "No model response"}${details.provider ? ` (${details.provider})` : ""}.`));
      const inspection = node("details");
      inspection.append(node("summary", "Execution details"));
      inspection.append(node("pre", JSON.stringify(details, null, 2)));
      element.append(inspection);
      container.append(element);
    }
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
    else if (!reason && principal?.role !== "flight_director") message = "Sign in under Approval access to approve this briefing.";
    else if (!reason && !acknowledged) message = "Explicitly acknowledge every flagged check and its listed findings.";
    else if (!reason) message = `Ready to approve this briefing. The launch verdict will remain ${selectedReport.verdict === "go" ? "GO" : "NO-GO"}.`;
    setText("approval-status", message);
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
