/*
 * Driver documents UI: collapse/expand a document's file rows, and "download
 * files without zipping" — fire one browser download per file.
 *
 * Event-delegated so it survives htmx swaps; no per-element wiring.
 */
(function () {
  "use strict";

  function toggleDocRow(row) {
    var uuid = row.getAttribute("data-doc-toggle");
    var filesRow = document.getElementById("files-" + uuid);
    if (!filesRow) return;
    var open = row.classList.toggle("expanded");
    filesRow.hidden = !open;
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

  function switchTab(btn, updateUrl) {
    var group = btn.closest("[data-tabs]");
    if (!group) return;
    var target = btn.getAttribute("data-tab-target");
    group.querySelectorAll("[data-tab-target]").forEach(function (b) {
      b.classList.toggle("is-active", b === btn);
    });
    group.querySelectorAll("[data-tab-panel]").forEach(function (p) {
      p.hidden = p.getAttribute("data-tab-panel") !== target;
    });
    // Remember the open tab in the URL fragment so a reload / redirect that keeps
    // it (activateFromHash) restores the same tab. replaceState keeps the path +
    // query and doesn't scroll or spam history.
    if (updateUrl !== false && window.history && history.replaceState) {
      history.replaceState(null, "", "#" + target);
    }
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

  // --- Inbox recognition (§8.2) --------------------------------------------
  //
  // Per-file "Recognize" recognises one file and drops its entry into the
  // confirm list as soon as it finishes. ("Recognize all" is handled server-side
  // — a single HTMX request that recognises the batch concurrently with
  // asyncio.gather and returns all entries together.) Files whose format isn't in
  // the PRD catalogue come back with X-Doc-Format: "unrecognized" and stay flagged.

  function csrfHeaders() {
    var headers = {};
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) headers["X-CSRFToken"] = meta.content;
    return headers;
  }

  function rowFor(filename) {
    var rows = document.querySelectorAll("[data-inbox-row]");
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("data-filename") === filename) return rows[i];
    }
    return null;
  }

  function recognizeOne(url, filename) {
    var form = new FormData();
    form.append("filename", filename);
    return fetch(url, {
      method: "POST",
      body: form,
      headers: csrfHeaders(),
      credentials: "same-origin",
    })
      .then(function (resp) {
        var fmt = resp.headers.get("X-Doc-Format");
        return resp.text().then(function (html) {
          return { fmt: fmt, html: html };
        });
      })
      .then(function (res) {
        var row = rowFor(filename);
        if (res.fmt === "recognized" && res.html) {
          var target = document.getElementById("confirm-entries");
          if (target) {
            target.insertAdjacentHTML("beforeend", res.html);
            var added = target.lastElementChild;
            if (window.htmx && added) htmx.process(added);
          }
          if (row) {
            row.classList.add("is-done");
            row.classList.remove("is-unrecognized");
          }
        } else {
          // Unrecognised format — keep it in the inbox, flagged.
          if (row) row.classList.add("is-unrecognized");
        }
      })
      .catch(function () {
        /* a single file failing shouldn't abort the batch */
      });
  }

  // Honour #tab-<name> in the URL on load so a full-page reload (calendar month
  // navigation, form redirects) keeps the right tab open instead of resetting.
  function activateFromHash() {
    var hash = (location.hash || "").replace(/^#/, "");
    if (!hash) return;
    var btn = document.querySelector('[data-tab-target="' + hash + '"]');
    if (btn) switchTab(btn, false);
  }
  document.addEventListener("DOMContentLoaded", activateFromHash);

  document.addEventListener("click", function (e) {
    var recOne = e.target.closest("[data-recognize-one]");
    if (recOne) {
      e.preventDefault();
      recOne.disabled = true;
      recognizeOne(recOne.getAttribute("data-url"), recOne.getAttribute("data-filename"))
        .then(function () { recOne.disabled = false; });
      return;
    }

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

    // Clicking a document row expands/collapses its files — but not when the
    // click landed on an action (link, button, form control).
    var docRow = e.target.closest("[data-doc-toggle]");
    if (docRow && !e.target.closest("a, button, input, select, label, form")) {
      toggleDocRow(docRow);
    }
  });
})();
