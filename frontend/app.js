const API_BASE = "http://127.0.0.1:8010";

const diagramInput = document.querySelector("#diagram");
const fileMeta = document.querySelector("#fileMeta");
const preview = document.querySelector("#preview");
const form = document.querySelector("#estimateForm");
const result = document.querySelector("#result");
const statusBadge = document.querySelector("#status");
const submitButton = form.querySelector("button[type='submit']");
const extractButton = document.querySelector("#extractButton");
const prevFieldButton = document.querySelector("#prevField");
const nextFieldButton = document.querySelector("#nextField");
const fieldFocus = document.querySelector("#fieldFocus");
const requiredEstimateFields = [
  "square_tube_length_mm",
  "square_tube_outer_mm",
  "square_tube_thickness_mm",
  "bottom_plate_l_mm",
  "bottom_plate_w_mm",
  "bottom_plate_t_mm",
  "top_plate_l_mm",
  "top_plate_w_mm",
  "top_plate_t_mm",
  "handle_od_mm",
  "handle_thickness_mm",
  "handle_length_mm",
  "screw_piece_dia_mm",
  "screw_piece_length_mm",
  "screw_piece_qty",
  "chair_angle_length_mm",
  "cutting_length_mm",
  "weld_length_mm",
  "bend_count",
];
const dimensionFields = [
  ["part_name", "Part name"],
  ["square_tube_length_mm", "Square tube length"],
  ["square_tube_outer_mm", "Square tube outer"],
  ["square_tube_thickness_mm", "Square tube thickness"],
  ["cutting_length_mm", "Total cut length"],
  ["bottom_plate_l_mm", "Bottom plate L"],
  ["bottom_plate_w_mm", "Bottom plate W"],
  ["bottom_plate_t_mm", "Bottom plate T"],
  ["top_plate_l_mm", "Top plate L"],
  ["top_plate_w_mm", "Top plate W"],
  ["top_plate_t_mm", "Top plate T"],
  ["handle_od_mm", "Handle OD"],
  ["handle_thickness_mm", "Handle thickness"],
  ["handle_length_mm", "Handle length"],
  ["chair_angle_weight_per_m", "Angle kg/m"],
  ["chair_angle_length_mm", "Angle length"],
  ["screw_piece_dia_mm", "Screw dia"],
  ["screw_piece_length_mm", "Screw length"],
  ["screw_piece_qty", "Screw qty"],
  ["weld_length_mm", "Weld length"],
  ["bend_count", "Bend count"],
];
const optionalDefaults = {
  chair_angle_weight_per_m: "2.42",
  bend_count: "0",
  press_machine_hits: "0",
};
let focusedFieldIndex = 0;
let formulaLookup = {};

diagramInput.addEventListener("change", async () => {
  const file = diagramInput.files?.[0];
  if (!file) {
    fileMeta.textContent = "No file selected";
    preview.innerHTML = "";
    clearExtractedFields();
    return;
  }

  clearExtractedFields();
  fileMeta.textContent = `${file.name} - ${(file.size / 1024).toFixed(1)} KB`;
  await renderDiagramPreview(file);
});

extractButton.addEventListener("click", async () => {
  const file = diagramInput.files?.[0];
  if (!file) {
    statusBadge.textContent = "Missing file";
    result.innerHTML = `<div class="empty-state">Select a diagram before extracting dimensions.</div>`;
    return;
  }

  const data = new FormData();
  data.append("diagram", file);

  extractButton.disabled = true;
  submitButton.disabled = true;
  statusBadge.textContent = "Extracting";

  try {
    const response = await fetch(`${API_BASE}/extract-dimensions`, {
      method: "POST",
      body: data,
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `API returned ${response.status}`);
    }

    fillForm(payload);
    applyOptionalDefaults();
    updateFieldFocus(0);
    statusBadge.textContent = "Extracted";
    result.innerHTML = `
      <p><strong>Dimensions extracted</strong></p>
      <p class="subtle">Source: Gemini API. Review the fields, correct anything uncertain, then calculate cost.</p>
      <ul class="assumptions">
        <li>Confidence: ${Math.round((payload.confidence || 0) * 100)}%</li>
        ${(payload.notes || []).map((note) => `<li>${note}</li>`).join("")}
      </ul>
    `;
  } catch (error) {
    statusBadge.textContent = "Extract error";
    result.innerHTML = `<div class="empty-state">${error.message}. Check Gemini API key and backend logs.</div>`;
  } finally {
    extractButton.disabled = false;
    submitButton.disabled = false;
  }
});

prevFieldButton.addEventListener("click", () => {
  updateFieldFocus(focusedFieldIndex - 1);
});

nextFieldButton.addEventListener("click", () => {
  updateFieldFocus(focusedFieldIndex + 1);
});

result.addEventListener("click", (event) => {
  const button = event.target.closest(".formula-value");
  if (!button) {
    return;
  }
  showFormulaForValue(button);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = diagramInput.files?.[0];
  if (!file) {
    statusBadge.textContent = "Missing file";
    result.innerHTML = `<div class="empty-state">Select a diagram before calculating.</div>`;
    return;
  }
  applyOptionalDefaults();
  const missingFields = requiredEstimateFields.filter((name) => !form.elements[name]?.value);
  if (missingFields.length) {
    statusBadge.textContent = "Extract first";
    const missingLabels = missingFields.map((name) => getFieldLabel(name)).join(", ");
    result.innerHTML = `<div class="empty-state">Some required extracted fields are missing: ${missingLabels}. Review the diagram extraction before calculating.</div>`;
    return;
  }

  const data = new FormData(form);
  data.append("diagram", file);

  submitButton.disabled = true;
  statusBadge.textContent = "Calculating";

  try {
    const response = await fetch(`${API_BASE}/estimate`, {
      method: "POST",
      body: data,
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `API returned ${response.status}`);
    }

    const estimate = await response.json();
    renderEstimate(estimate);
    statusBadge.textContent = "Ready";
  } catch (error) {
    statusBadge.textContent = "Error";
    result.innerHTML = `<div class="empty-state">Could not calculate estimate: ${error.message}</div>`;
  } finally {
    submitButton.disabled = false;
  }
});

function money(value) {
  return `Rs ${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function registerFormula(step) {
  const id = `formula_${Object.keys(formulaLookup).length}`;
  formulaLookup[id] = step;
  return id;
}

function formulaButton(label, step, className = "") {
  if (!step) {
    return escapeHtml(label);
  }
  const id = registerFormula(step);
  return `<button type="button" class="formula-value ${className}" data-formula-id="${id}">${escapeHtml(label)}</button>`;
}

function formulaPanel(step) {
  return `
    <div class="inline-formula">
      <small>${escapeHtml(step.section)}</small>
      <strong>${escapeHtml(step.name)}</strong>
      <code>${escapeHtml(step.formula)}</code>
      <span>${escapeHtml(step.substituted_values)}</span>
      <b>${escapeHtml(step.result)}</b>
    </div>
  `;
}

function showFormulaForValue(button) {
  const step = formulaLookup[button.dataset.formulaId];
  if (!step) {
    return;
  }

  result.querySelectorAll(".inline-formula-row, .summary-formula").forEach((node) => node.remove());
  const row = button.closest("tr");
  if (row) {
    const formulaRow = document.createElement("tr");
    formulaRow.className = "inline-formula-row";
    formulaRow.innerHTML = `<td colspan="${row.children.length}">${formulaPanel(step)}</td>`;
    row.insertAdjacentElement("afterend", formulaRow);
    return;
  }

  const metric = button.closest(".metric");
  const summary = button.closest(".summary");
  if (metric && summary) {
    const detail = document.createElement("div");
    detail.className = "summary-formula";
    detail.innerHTML = formulaPanel(step);
    summary.insertAdjacentElement("afterend", detail);
  }
}

function fillForm(values) {
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined || key === "confidence" || key === "notes") {
      return;
    }

    const input = form.elements[key];
    if (input) {
      input.value = value;
    }
  });
}

function applyOptionalDefaults() {
  Object.entries(optionalDefaults).forEach(([name, value]) => {
    const input = form.elements[name];
    if (input && !input.value) {
      input.value = value;
    }
  });
}

function clearExtractedFields() {
  dimensionFields.forEach(([name]) => {
    const input = form.elements[name];
    if (input) {
      input.value = "";
    }
  });
  fieldFocus.textContent = "Extract dimensions to review fields.";
  statusBadge.textContent = "Waiting";
  result.innerHTML = `<div class="empty-state">Upload a diagram, extract dimensions, then calculate to see the cost breakdown.</div>`;
}

async function renderDiagramPreview(file) {
  const isTiff = /\.(tif|tiff)$/i.test(file.name);
  if (file.type.startsWith("image/") && !isTiff) {
    const url = URL.createObjectURL(file);
    preview.innerHTML = `<img src="${url}" alt="Uploaded diagram preview" />`;
    return;
  }

  const data = new FormData();
  data.append("diagram", file);
  preview.textContent = "Preparing preview...";
  try {
    const response = await fetch(`${API_BASE}/diagram-preview`, {
      method: "POST",
      body: data,
    });
    if (!response.ok) {
      throw new Error(`Preview returned ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    preview.innerHTML = `<img src="${url}" alt="Uploaded diagram preview" />`;
  } catch (error) {
    preview.innerHTML = `<span>${file.name}</span>`;
  }
}

function getFieldLabel(name) {
  return dimensionFields.find(([fieldName]) => fieldName === name)?.[1] || name;
}

function updateFieldFocus(nextIndex) {
  const total = dimensionFields.length;
  focusedFieldIndex = ((nextIndex % total) + total) % total;
  const [name, label] = dimensionFields[focusedFieldIndex];
  const input = form.elements[name];
  if (!input) {
    return;
  }

  input.scrollIntoView({ behavior: "smooth", block: "center" });
  input.focus({ preventScroll: true });
  fieldFocus.innerHTML = `
    <strong>${label}</strong>
    <span>${input.value || "Not extracted"}</span>
    <small>${focusedFieldIndex + 1} of ${total}</small>
  `;
}

function renderEstimate(estimate) {
  formulaLookup = {};
  const stepsByName = Object.fromEntries((estimate.calculation_steps || []).map((step) => [step.name, step]));
  const totalWeightFormula = {
    section: "Weight",
    name: "Total calculated weight",
    formula: "Total weight = sum of all item weights",
    substituted_values: estimate.items.map((item) => `${item.weight_kg} kg`).join(" + "),
    result: `${estimate.total_weight_kg} kg`,
  };
  result.innerHTML = `
    <p><strong>${estimate.part_name}</strong></p>
    <p class="subtle">${estimate.likely_use}</p>
    <div class="summary">
      <div class="metric"><small>Total cost</small><strong>${formulaButton(money(estimate.total_estimated_cost), stepsByName["Total estimated cost"], "metric-value")}</strong></div>
      <div class="metric"><small>Weight</small><strong>${formulaButton(`${estimate.total_weight_kg} kg`, totalWeightFormula, "metric-value")}</strong></div>
      <div class="metric"><small>Material</small><strong>${formulaButton(money(estimate.total_material_cost), stepsByName["Material cost"], "metric-value")}</strong></div>
      <div class="metric"><small>Process</small><strong>${formulaButton(money(estimate.total_process_cost), {
        section: "Process",
        name: "Total process cost",
        formula: "Process cost = cutting + bending + welding + press machine + tacking",
        substituted_values: `${money(estimate.process_breakdown.cutting_cost)} + ${money(estimate.process_breakdown.bending_cost)} + ${money(estimate.process_breakdown.welding_cost)} + ${money(estimate.process_breakdown.press_machine_cost)} + ${money(estimate.process_breakdown.tacking_cost)}`,
        result: money(estimate.total_process_cost),
      }, "metric-value")}</strong></div>
      <div class="metric"><small>Surface</small><strong>${formulaButton(money(estimate.surface_treatment_cost), stepsByName["Surface treatment cost"], "metric-value")}</strong></div>
      <div class="metric"><small>File</small><strong>${estimate.file_size_kb} KB</strong></div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Process</th>
          <th>Parameter</th>
          <th>Cost</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Cutting</td>
          <td>${estimate.process_breakdown.cutting_length_mm} mm</td>
          <td>${formulaButton(money(estimate.process_breakdown.cutting_cost), stepsByName["Cutting cost"])}</td>
        </tr>
        <tr>
          <td>Bending</td>
          <td>${estimate.process_breakdown.bend_count} bends</td>
          <td>${formulaButton(money(estimate.process_breakdown.bending_cost), stepsByName["Bending cost"])}</td>
        </tr>
        <tr>
          <td>Welding</td>
          <td>${estimate.process_breakdown.weld_length_mm} mm</td>
          <td>${formulaButton(money(estimate.process_breakdown.welding_cost), stepsByName["Welding cost"])}</td>
        </tr>
        <tr>
          <td>Press machine</td>
          <td>${estimate.process_breakdown.press_machine_hits} hits</td>
          <td>${formulaButton(money(estimate.process_breakdown.press_machine_cost), stepsByName["Press machine cost"])}</td>
        </tr>
        <tr>
          <td>Tacking</td>
          <td>Optional fixed</td>
          <td>${formulaButton(money(estimate.process_breakdown.tacking_cost), stepsByName["Tacking labor"])}</td>
        </tr>
      </tbody>
    </table>
    <table>
      <thead>
        <tr>
          <th>Item</th>
          <th>Qty</th>
          <th>Weight</th>
          <th>Material</th>
        </tr>
      </thead>
      <tbody>
        ${estimate.items
          .map(
            (item) => `
              <tr>
                <td>${item.name}</td>
                <td>${item.quantity}</td>
                <td>${formulaButton(`${item.weight_kg} kg`, item.formulas?.weight)}</td>
                <td>${formulaButton(money(item.material_cost), item.formulas?.material)}</td>
              </tr>
            `
          )
          .join("")}
      </tbody>
    </table>
    <ul class="assumptions">
      ${estimate.assumptions.map((assumption) => `<li>${assumption}</li>`).join("")}
    </ul>
    <h3>Formula Trail</h3>
    <div class="formula-list">
      ${(estimate.calculation_steps || [])
        .map(
          (step) => `
            <div class="formula-step">
              <small>${step.section}</small>
              <strong>${step.name}</strong>
              <code>${step.formula}</code>
              <span>${step.substituted_values}</span>
              <b>${step.result}</b>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}
