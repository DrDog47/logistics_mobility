/*
 * Driver documents UI: collapse/expand a document's file rows, and "download
 * files without zipping" — fire one browser download per file.
 *
 * Event-delegated so it survives htmx swaps; no per-element wiring.
 */
(function () {
  "use strict";

  function toggleFiles(uuid, btn) {
    var rows = document.querySelectorAll('tr[data-files-for="' + uuid + '"]');
    var expanded = btn.getAttribute("aria-expanded") === "true";
    rows.forEach(function (row) {
      row.hidden = expanded;
    });
    btn.setAttribute("aria-expanded", String(!expanded));
    btn.classList.toggle("is-open", !expanded);
  }

  function downloadUrls(urls) {
    urls.forEach(function (url, i) {
      // Stagger so the browser doesn't merge/ignore rapid-fire downloads.
      setTimeout(function () {
        var a = document.createElement("a");
        a.href = url;
        a.setAttribute("download", "");
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }, i * 350);
    });
  }

  function urlsFrom(nodeList) {
    var urls = [];
    nodeList.forEach(function (a) {
      var u = a.getAttribute("data-dl-url");
      if (u) urls.push(u);
    });
    return urls;
  }

  function switchTab(btn) {
    var group = btn.closest("[data-tabs]");
    if (!group) return;
    var target = btn.getAttribute("data-tab-target");
    group.querySelectorAll("[data-tab-target]").forEach(function (b) {
      b.classList.toggle("is-active", b === btn);
    });
    group.querySelectorAll("[data-tab-panel]").forEach(function (p) {
      p.hidden = p.getAttribute("data-tab-panel") !== target;
    });
  }

  function copyValue(btn) {
    var text = btn.getAttribute("data-copy");
    if (!text) return;
    var done = function () {
      var old = btn.textContent;
      btn.textContent = "✓";
      setTimeout(function () { btn.textContent = old; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {});
    }
  }

  // Honour #tab-<name> in the URL on load so a full-page reload (calendar month
  // navigation, form redirects) keeps the right tab open instead of resetting.
  function activateFromHash() {
    var hash = (location.hash || "").replace(/^#/, "");
    if (!hash) return;
    var btn = document.querySelector('[data-tab-target="' + hash + '"]');
    if (btn) switchTab(btn);
  }
  document.addEventListener("DOMContentLoaded", activateFromHash);

  document.addEventListener("click", function (e) {
    var tab = e.target.closest("[data-tab-target]");
    if (tab) {
      switchTab(tab);
      return;
    }

    var copy = e.target.closest("[data-copy]");
    if (copy) {
      copyValue(copy);
      return;
    }

    var toggle = e.target.closest("[data-files-toggle]");
    if (toggle) {
      toggleFiles(toggle.getAttribute("data-files-toggle"), toggle);
      return;
    }

    var perDoc = e.target.closest("[data-download-doc]");
    if (perDoc) {
      var uuid = perDoc.getAttribute("data-download-doc");
      downloadUrls(
        urlsFrom(document.querySelectorAll('tr[data-files-for="' + uuid + '"] a[data-dl-url]'))
      );
      return;
    }

    var all = e.target.closest("[data-download-all]");
    if (all) {
      var scope = document.querySelector(all.getAttribute("data-scope"));
      if (scope) downloadUrls(urlsFrom(scope.querySelectorAll("a[data-dl-url]")));
      return;
    }
  });
})();
