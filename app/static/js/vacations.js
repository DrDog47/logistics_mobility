/*
 * Vacations module UI — shared by the fleet vacations page and the per-driver
 * Vacations tab. Drives the add/edit leave modal and the all-leaves table's
 * search + status filter. Event-delegated and null-safe, so it no-ops on pages
 * that don't render these elements (and survives htmx swaps of the panel).
 */
(function () {
  "use strict";

  function selectType(modal, value) {
    modal.querySelectorAll("[data-type-radio]").forEach(function (r) {
      var input = r.querySelector("input");
      var on = input.value === value;
      input.checked = on;
      r.classList.toggle("is-selected", on);
    });
  }

  function openModal(modal) {
    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }
  function closeModal(modal) {
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  function openAdd(modal) {
    var form = modal.querySelector("#vac-form");
    var title = modal.querySelector("#vac-modal-title");
    var driverField = modal.querySelector("[data-vac-driver-field]");
    var driverSelect = modal.querySelector("#vac-driver-select");
    var driverStatic = modal.querySelector("[data-vac-driver-static]");

    form.action = form.getAttribute("data-add-action") || form.action;
    if (title) title.textContent = modal.getAttribute("data-title-add") || "Add";
    if (driverField) driverField.hidden = false;
    if (driverSelect) driverSelect.disabled = false;
    if (driverStatic) driverStatic.hidden = true;
    form.reset();
    selectType(modal, "annual");
    openModal(modal);
  }

  function openEdit(modal, btn) {
    var form = modal.querySelector("#vac-form");
    var title = modal.querySelector("#vac-modal-title");
    var driverField = modal.querySelector("[data-vac-driver-field]");
    var driverSelect = modal.querySelector("#vac-driver-select");
    var driverStatic = modal.querySelector("[data-vac-driver-static]");

    form.action = btn.getAttribute("data-action");
    if (title) title.textContent = modal.getAttribute("data-title-edit") || "Edit";
    // Driver is fixed for an existing leave — hide the picker, show it as text.
    if (driverSelect) driverSelect.disabled = true;
    if (driverField) driverField.hidden = true;
    if (driverStatic) {
      driverStatic.hidden = false;
      driverStatic.textContent = btn.getAttribute("data-driver") || "";
    }
    selectType(modal, btn.getAttribute("data-kind") || "annual");
    modal.querySelector("#vac-start").value = btn.getAttribute("data-start") || "";
    modal.querySelector("#vac-end").value = btn.getAttribute("data-end") || "";
    modal.querySelector("#vac-note").value = btn.getAttribute("data-note") || "";
    openModal(modal);
  }

  document.addEventListener("click", function (e) {
    var modal = document.getElementById("vac-modal");
    if (!modal) return;

    if (e.target.closest("[data-vac-add]")) { openAdd(modal); return; }
    var edit = e.target.closest("[data-vac-edit]");
    if (edit) { openEdit(modal, edit); return; }
    if (e.target.closest("[data-vac-close]")) { closeModal(modal); return; }
    if (e.target === modal) { closeModal(modal); return; }
    var radio = e.target.closest("[data-type-radio]");
    if (radio && modal.contains(radio)) {
      selectType(modal, radio.querySelector("input").value);
    }
  });

  document.addEventListener("keydown", function (e) {
    var modal = document.getElementById("vac-modal");
    if (modal && !modal.hidden && e.key === "Escape") closeModal(modal);
  });

  // --- All-leaves table: client-side search + status filter ----------------
  function applyFilter() {
    var search = document.querySelector("[data-vac-search]");
    var active = document.querySelector("[data-vac-filter].is-active");
    var filter = active ? active.getAttribute("data-vac-filter") : "all";
    var q = (search && search.value || "").trim().toLowerCase();
    document.querySelectorAll("[data-vac-row]").forEach(function (r) {
      var okStatus = filter === "all" || r.getAttribute("data-status") === filter;
      var okName = !q || (r.getAttribute("data-name") || "").indexOf(q) !== -1;
      r.hidden = !(okStatus && okName);
    });
  }

  document.addEventListener("input", function (e) {
    if (e.target.closest("[data-vac-search]")) applyFilter();
  });
  document.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-vac-filter]");
    if (!chip) return;
    var group = chip.parentElement;
    group.querySelectorAll("[data-vac-filter]").forEach(function (c) {
      c.classList.toggle("is-active", c === chip);
    });
    applyFilter();
  });
})();
