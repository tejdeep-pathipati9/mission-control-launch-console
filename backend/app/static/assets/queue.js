"use strict";

(() => {
  const { requestJson, setText, validId } = window.MissionControl;
  const { utcTime, usd, toneForStatus } = window.MissionReportView;
  const pageSize = 25;
  const statusLabels = { processing: "Processing", pending_review: "Pending review", approved: "Approved", failed: "Failed" };
  let offset = 0;
  let rows = [];
  let hasNext = false;
  let queueBusy = false;
  let queueGeneration = 0;
  let queueController = null;
  let queueTimer = null;
  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
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
      link.href = `/reports/${report.id}`;
      link.className = "report-id";
      idCell.append(link);
      row.append(idCell);
      const station = node("td");
      station.append(node("span", report.station));
      station.append(node("small", report.engineer_name));
      row.append(station);
      const status = node("td");
      const badge = node("span", statusLabels[report.status] ?? report.status);
      badge.className = `status-badge ${toneForStatus(report.status)}`;
      status.append(badge);
      row.append(status);
      const verdict = node("td");
      const decision = node("span", report.verdict === "go" ? "GO" : "NO-GO");
      decision.className = `status-badge ${report.verdict === "no_go" ? "danger" : ""}`;
      verdict.append(decision);
      verdict.className = "verdict-cell";
      row.append(verdict);
      const deadline = node("td");
      deadline.className = "deadline-cell";
      deadline.title = utcTime(report.brief_by_at);
      const date = report.brief_by_at && new Date(report.brief_by_at);
      if (date && !Number.isNaN(date.getTime())) {
        deadline.append(node("span", date.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" })));
        deadline.append(node("small", date.toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" })));
      } else deadline.textContent = report.status === "processing" ? "Pending" : "Unavailable";
      row.append(deadline);
      const flags = node("td");
      const flagCount = report.flags?.length ?? 0;
      const flag = node("span", flagCount ? String(flagCount) : "—");
      if (flagCount) flag.className = "status-badge warning";
      flags.append(flag);
      row.append(flags);
      const costs = report.costs;
      const cost = node("td");
      cost.className = "cost-cell";
      cost.title = costs?.cost_complete ? "Complete recorded cost" : "Usage or pricing is incomplete";
      if (costs?.cost_complete) cost.append(node("span", usd(costs.total_cost_usd).replace(" USD", "")));
      else if (costs?.known_cost_usd != null && Number(costs.known_cost_usd) > 0) {
        cost.append(node("span", usd(costs.known_cost_usd).replace(" USD", "")));
        cost.append(node("small", "Partial cost"));
      } else {
        cost.append(node("span", report.status === "processing" ? "Pending" : "Unavailable"));
      }
      row.append(cost);
      body.append(row);
    }
    if (focusId?.startsWith("queue-report-")) document.getElementById(focusId)?.focus();
    setText("queue-range", rows.length ? `${offset + 1}–${offset + rows.length}` : "No reports on this page");
    setText("queue-status", rows.length ? `${rows.length} reports shown.` : (offset ? "No reports on this page. Use Previous to return." : "No reports yet. Submit a report to begin."));
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

  document.getElementById("refresh-queue").addEventListener("click", () => loadQueue());
  document.getElementById("previous-page").addEventListener("click", () => { if (!queueBusy && offset > 0) loadQueue(offset - pageSize); });
  document.getElementById("next-page").addEventListener("click", () => { if (!queueBusy && hasNext) loadQueue(offset + pageSize); });
  window.addEventListener("pagehide", () => {
    queueGeneration += 1;
    queueController?.abort();
    clearTimeout(queueTimer);
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) loadQueue(); });
  loadQueue();
})();
