"use strict";

(() => {
  const { setText, requestJson, validId } = window.MissionControl;
  const form = document.querySelector("#report-form");
  const fields = document.querySelector("#report-fields");
  const submitButton = document.querySelector("#submit-report");
  const errorSection = document.querySelector("#form-errors");
  const textFields = { engineer_name: 120, station: 120, note: 10000 };
  const numberFields = ["wind_knots", "temp_c", "fuel_pct"];
  const labels = {
    engineer_name: "Engineer name", station: "Station", note: "Anomaly note",
    wind_knots: "Wind", temp_c: "Temperature", fuel_pct: "Fuel",
  };
  let generation = 0;
  let requestController = null;
  let navigating = false;

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


  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (fields.disabled) return;
    clearErrors();
    const { payload, errors } = collectReport();
    if (errors.length) { showErrors(errors); return; }
    const attempt = ++generation;
    requestController = new AbortController();
    fields.disabled = true;
    submitButton.textContent = "Submitting…";
    form.setAttribute("aria-busy", "true");
    try {
      const accepted = await requestJson("/api/reports", {
        signal: requestController.signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (attempt !== generation) return;
      if (!validId(accepted.report_id)) throw new Error("Submission could not be confirmed.");
      navigating = true;
      submitButton.textContent = "Opening report…";
      location.assign(`/reports/${accepted.report_id}`);
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
      if (attempt === generation && !navigating) {
        submitButton.textContent = "Submit report";
        form.removeAttribute("aria-busy");
      }
    }
  });

  window.addEventListener("pagehide", () => {
    generation += 1;
    requestController?.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    if (navigating) {
      form.reset();
      clearErrors();
      navigating = false;
    } else if (fields.disabled) {
      showErrors([{ message: "Submission was interrupted and may have been saved. Confirm before submitting again." }]);
    }
    fields.disabled = false;
    submitButton.textContent = "Submit report";
    form.removeAttribute("aria-busy");
  });
  submitButton.disabled = false;
})();
