"use strict";

(() => {
  const { requestJson, setText, validId } = window.MissionControl;
  const { renderReport, utcTime, usd, checkLabels } = window.MissionReportView;
  const pageSize = 25;
  const statusLabels = { processing: "Processing", pending_review: "Pending review", approved: "Approved", failed: "Failed" };
  let offset = 0;
  let rows = [];
  let hasNext = false;
  let queueBusy = false;
  let queueGeneration = 0;
  let queueController = null;
  let queueTimer = null;
  let selectedId = null;
  let selectedReport = null;
  let detailGeneration = 0;
  let detailController = null;
  let detailTimer = null;

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

  function queueControls() {
    document.getElementById("previous-page").disabled = queueBusy || offset === 0;
    document.getElementById("next-page").disabled = queueBusy || !hasNext;
    document.getElementById("refresh-queue").disabled = queueBusy;
  }

  function renderQueue() {
    const focusId = document.activeElement?.id;
    const body = document.getElementById("queue-body");
    body.replaceChildren();
    for (const report of rows) {
      const row = node("tr");
      const idCell = node("td");
      const link = node("a", `#${report.id}`);
      link.id = `queue-report-${report.id}`;
      link.href = `/director?report=${report.id}`;
      if (report.id === selectedId) link.setAttribute("aria-current", "true");
      link.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || (event.button ?? 0) !== 0) return;
        event.preventDefault();
        selectReport(report.id, true);
      });
      idCell.append(link);
      row.append(idCell);
      const costs = report.costs;
      for (const value of [
        report.station, statusLabels[report.status] ?? report.status,
        report.verdict === "go" ? "GO" : "NO-GO", utcTime(report.brief_by_at),
        String(report.flags?.length ?? 0),
        costs?.cost_complete ? usd(costs.total_cost_usd) : `Incomplete; known subtotal ${usd(costs?.known_cost_usd)}`,
      ]) row.append(node("td", value));
      body.append(row);
    }
    if (focusId?.startsWith("queue-report-")) document.getElementById(focusId)?.focus();
    setText("queue-range", rows.length ? `${offset + 1}–${offset + rows.length}` : "No reports on this page");
    setText("queue-status", rows.length ? `${rows.length} reports shown.` : "No reports yet. Submit a report to begin.");
    queueControls();
  }

  async function loadQueue(nextOffset = offset) {
    const generation = ++queueGeneration;
    clearTimeout(queueTimer);
    queueController?.abort();
    queueController = new AbortController();
    if (nextOffset !== offset) {
      rows = [];
      document.getElementById("queue-body").replaceChildren();
      setText("queue-range", "");
    }
    offset = nextOffset;
    queueBusy = true;
    queueControls();
    document.getElementById("queue-error").hidden = true;
    try {
      const result = await requestJson(`/api/reports?limit=${pageSize + 1}&offset=${offset}`, { signal: queueController.signal });
      if (generation !== queueGeneration) return;
      if (!Array.isArray(result) || result.some((report) => !validId(report.id))) throw new Error("Invalid queue response");
      hasNext = result.length > pageSize;
      rows = result.slice(0, pageSize);
      renderQueue();
      queueTimer = setTimeout(() => loadQueue(), 5000);
    } catch {
      if (generation !== queueGeneration) return;
      setText("queue-error", "Queue updates paused. Use Refresh queue to try again.");
      document.getElementById("queue-error").hidden = false;
    } finally {
      if (generation === queueGeneration) {
        queueBusy = false;
        queueControls();
      }
    }
  }

  function renderChecks(report) {
    const container = document.getElementById("check-cards");
    container.replaceChildren();
    for (const check of report.checks) {
      const element = card(`${checkLabels[check.check_type] ?? check.check_type} — ${check.outcome}`);
      element.append(node("p", check.reason));
      element.append(list(check.details_json?.findings ?? []));
      if (check.details_json?.error_reason) element.append(node("p", `Evaluation unresolved: ${check.details_json.error_reason}`));
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
    for (const [title, costs] of [["All report attempts", report.costs], ["Current attempt", report.current_run]]) {
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
      const name = step.step_name.replaceAll("_", " ");
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

  async function loadDetail(generation, focus = false) {
    document.getElementById("refresh-report").disabled = true;
    document.getElementById("poll-error").hidden = true;
    detailController = new AbortController();
    try {
      const report = await requestJson(`/api/reports/${selectedId}`, { signal: detailController.signal });
      if (generation !== detailGeneration) return;
      renderReport(report, selectedId);
      selectedReport = report;
      renderChecks(report);
      renderHistory(report);
      renderCosts(report);
      renderTrace(report);
      rows = rows.map((row) => row.id === report.id ? report : row);
      renderQueue();
      if (focus) document.getElementById("report-heading").focus();
      if (report.status === "processing") detailTimer = setTimeout(() => loadDetail(generation), 2000);
    } catch (error) {
      if (generation !== detailGeneration) return;
      setText("poll-error-message", error.status === 404 ? "Report not found. Select another report." : "Report updates paused. Refresh this report to try again.");
      document.getElementById("retry-status").hidden = error.status === 404;
      document.getElementById("poll-error").hidden = false;
    } finally {
      if (generation === detailGeneration) document.getElementById("refresh-report").disabled = false;
    }
  }

  function selectReport(id, focus = false) {
    const generation = ++detailGeneration;
    clearTimeout(detailTimer);
    detailController?.abort();
    selectedId = id;
    selectedReport = null;
    setText("report-heading", `Report #${id}`);
    setText("status-message", "Loading report status…");
    document.getElementById("report-status").hidden = false;
    document.getElementById("report-result").hidden = true;
    document.getElementById("selection-hint").hidden = true;
    document.getElementById("report-link").href = `/reports/${id}`;
    history.replaceState(null, "", `/director?report=${id}`);
    loadDetail(generation, focus);
  }

  function refreshDetail() {
    if (selectedId === null) return;
    detailController?.abort();
    clearTimeout(detailTimer);
    loadDetail(++detailGeneration);
  }

  document.getElementById("refresh-queue").addEventListener("click", () => loadQueue());
  document.getElementById("previous-page").addEventListener("click", () => { if (!queueBusy && offset > 0) loadQueue(offset - pageSize); });
  document.getElementById("next-page").addEventListener("click", () => { if (!queueBusy && hasNext) loadQueue(offset + pageSize); });
  document.getElementById("refresh-report").addEventListener("click", refreshDetail);
  document.getElementById("retry-status").addEventListener("click", refreshDetail);
  window.addEventListener("pagehide", () => {
    queueGeneration += 1;
    detailGeneration += 1;
    queueController?.abort();
    detailController?.abort();
    clearTimeout(queueTimer);
    clearTimeout(detailTimer);
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { loadQueue(); refreshDetail(); }
  });
  loadQueue();
  const requested = new URLSearchParams(location.search).get("report");
  if (requested !== null) {
    if (/^[1-9]\d*$/.test(requested) && validId(Number(requested))) selectReport(Number(requested));
    else setText("selection-hint", "Invalid report link. Select a report from the queue.");
  }
})();
