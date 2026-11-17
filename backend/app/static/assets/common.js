"use strict";

(() => {
  function setText(id, value) {
    const element = document.getElementById(id);
    if (element.textContent !== value) element.textContent = value;
  }

  async function requestJson(path, options = {}) {
    const controller = new AbortController();
    const callerSignal = options.signal;
    const abort = () => controller.abort();
    if (callerSignal?.aborted) controller.abort();
    callerSignal?.addEventListener("abort", abort, { once: true });
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
      callerSignal?.removeEventListener("abort", abort);
    }
  }

  function validId(value) {
    return Number.isSafeInteger(value) && value > 0;
  }

  window.MissionControl = Object.freeze({ setText, requestJson, validId });
})();
