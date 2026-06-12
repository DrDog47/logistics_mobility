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
    // Anchor the scroll: panels have very different heights (a tall Documents
    // tab vs. a one-line History placeholder), so swapping them changes the
    // page height. If the page shrinks while scrolled down, the browser clamps
    // scrollTop and the whole page visibly jumps upward. Capture the tabs bar's
    // viewport position before the swap and counter-scroll after, so it stays
    // put and the switch feels in-place.
    var beforeTop = group.getBoundingClientRect().top;
    group.querySelectorAll("[data-tab-target]").forEach(function (b) {
      b.classList.toggle("is-active", b === btn);
    });
    group.querySelectorAll("[data-tab-panel]").forEach(function (p) {
      p.hidden = p.getAttribute("data-tab-panel") !== target;
    });
    var delta = group.getBoundingClientRect().top - beforeTop;
    if (delta) window.scrollBy(0, delta);
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

  // --- Recognition queue (client-side worker pool) -------------------------
  //
  // Pressing "Recognize" (one file) or "Recognize all" pushes files onto a
  // shared queue created on the first press. A small pool of workers drains it,
  // recognising each file via the (async) recognize-one endpoint, and inserts
  // its confirm-&-edit entry as soon as THAT file finishes — so the first file
  // done appears first instead of waiting for the whole batch. Concurrency is
  // capped so the recognizer / LLM isn't hammered.
  var RECOGNIZE_CONCURRENCY = 4;
  var recognizeQueue = [];
  var recognizeActive = 0;
  var recognizeSeen = Object.create(null); // filename -> true (queued / in-flight / done)

  function pumpRecognition() {
    while (recognizeActive < RECOGNIZE_CONCURRENCY && recognizeQueue.length) {
      runRecognizeJob(recognizeQueue.shift());
    }
  }

  function runRecognizeJob(job) {
    recognizeActive++;
    var row = rowFor(job.filename);
    if (row) {
      row.classList.remove("is-queued");
      row.classList.add("is-recognizing");
    }
    // recognizeOne never rejects (it swallows errors), so .then always runs.
    recognizeOne(job.url, job.filename).then(function () {
      var r = rowFor(job.filename);
      if (r) r.classList.remove("is-recognizing");
      recognizeActive--;
      pumpRecognition();
    });
  }

  function enqueueRecognition(url, filename) {
    if (!filename || recognizeSeen[filename]) return;
    recognizeSeen[filename] = true;
    var row = rowFor(filename);
    if (row) {
      row.classList.add("is-queued");
      var btn = row.querySelector("[data-recognize-one]");
      if (btn) btn.disabled = true;
    }
    recognizeQueue.push({ url: url, filename: filename });
    pumpRecognition();
  }

  function enqueueAllRecognition(url) {
    document.querySelectorAll("[data-inbox-row][data-filename]").forEach(function (row) {
      if (row.classList.contains("is-done")) return;
      enqueueRecognition(url, row.getAttribute("data-filename"));
    });
  }

  // Activate the Documents tab from anywhere on the page (e.g. the document
  // summary, which lives outside the [data-tabs] group). No-op if already open.
  function openDocumentsTab() {
    var btn = document.querySelector('[data-tab-target="documents"]');
    if (btn && !btn.classList.contains("is-active")) switchTab(btn);
    return btn;
  }

  // Summary row → jump to its document in the Documents tab: expand the row,
  // scroll it into view and flash it. Runs after a tick so the tab is visible
  // (a hidden panel can't be scrolled to).
  function revealDocument(uuid) {
    openDocumentsTab();
    setTimeout(function () {
      var row = document.querySelector('[data-doc-toggle="' + uuid + '"]');
      var filesRow = document.getElementById("files-" + uuid);
      if (!row) return;
      if (filesRow && !row.classList.contains("expanded")) {
        row.classList.add("expanded");
        filesRow.hidden = false;
      }
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("row-highlight");
      setTimeout(function () { row.classList.remove("row-highlight"); }, 1800);
    }, 60);
  }

  // Missing document → scroll to the drag-and-drop upload zone and flash it.
  function revealTarget(selector) {
    openDocumentsTab();
    setTimeout(function () {
      var el = document.querySelector(selector);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("is-flashing");
      setTimeout(function () { el.classList.remove("is-flashing"); }, 1600);
    }, 60);
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
      enqueueRecognition(recOne.getAttribute("data-url"), recOne.getAttribute("data-filename"));
      return;
    }

    var recAll = e.target.closest("[data-recognize-all]");
    if (recAll) {
      e.preventDefault();
      enqueueAllRecognition(recAll.getAttribute("data-url"));
      return;
    }

    // Missing-document button in the summary → scroll to the upload zone.
    // Checked before the summary-row handler so the button doesn't also expand.
    var scrollBtn = e.target.closest("[data-scroll-target]");
    if (scrollBtn) {
      e.preventDefault();
      revealTarget(scrollBtn.getAttribute("data-scroll-target"));
      return;
    }

    // Summary row → reveal the matching document (ignore clicks on inner controls).
    var sumRow = e.target.closest("[data-summary-target]");
    if (sumRow && !e.target.closest("a, button")) {
      revealDocument(sumRow.getAttribute("data-summary-target"));
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
