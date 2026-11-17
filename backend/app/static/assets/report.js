"use strict";

(() => {
  const { setText, requestJson, validId } = window.MissionControl;
  const { renderReport } = window.MissionReportView;
  const pollError = document.querySelector("#poll-error");
  let generation = 0;
  let pollTimer = null;
  let requestController = null;
  const match = location.pathname.match(/^\/reports\/([1-9]\d*)\/?$/);
  const reportId = match && validId(Number(match[1])) ? Number(match[1]) : null;

  function stopRequests() {
    generation += 1;
    clearTimeout(pollTimer);
    pollTimer = null;
    requestController?.abort();
    requestController = null;
  }

  async function pollReport(attempt) {
    if (attempt !== generation || reportId === null) return;
    pollError.hidden = true;
    document.querySelector("#retry-status").disabled = true;
    try {
      requestController = new AbortController();
      const report = await requestJson(`/api/reports/${reportId}`, { signal: requestController.signal });
      if (attempt !== generation) return;
      renderReport(report, reportId);
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
    document.querySelector("#director-link").href = `/reports?report=${reportId}`;
    pollReport(generation);
  }
})();
