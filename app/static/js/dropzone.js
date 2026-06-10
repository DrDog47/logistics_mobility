/*
 * Drag-and-drop document upload zone — UI only.
 *
 * Stages files client-side (drag/drop or file picker), lists them, lets the
 * user remove/clear. No upload, no recognition yet — the "Process" button stays
 * disabled until the backend pipeline lands (see PRD §8).
 *
 * Supports multiple independent zones on one page; each [data-dropzone] is wired
 * separately. Re-initialises on htmx swaps.
 */
(function () {
  "use strict";

  var ACCEPT_EXT = ["pdf", "jpg", "jpeg", "png"];

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function extOf(name) {
    var i = name.lastIndexOf(".");
    return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
  }

  function initZone(zone) {
    if (zone.dataset.dzReady === "1") return;
    zone.dataset.dzReady = "1";

    var area = zone.querySelector("[data-dropzone-area]");
    var input = zone.querySelector("[data-dropzone-input]");
    var list = zone.querySelector("[data-dropzone-list]");
    var filesEl = zone.querySelector("[data-dropzone-files]");
    var countEl = zone.querySelector("[data-dropzone-count]");
    var clearBtn = zone.querySelector("[data-dropzone-clear]");
    var processBtn = zone.querySelector("[data-dropzone-process]");
    var statusEl = zone.querySelector("[data-dropzone-status]");
    var uploadUrl = zone.dataset.uploadUrl || "";
    var inboxUrl = zone.dataset.inboxUrl || "";
    var driverName = zone.dataset.driverName || "";

    // Staged files for this zone. Keyed by name+size+lastModified to dedupe.
    var staged = new Map();

    function keyFor(file) {
      return file.name + "|" + file.size + "|" + file.lastModified;
    }

    function render() {
      filesEl.innerHTML = "";
      staged.forEach(function (file, key) {
        var ext = extOf(file.name);
        var ok = ACCEPT_EXT.indexOf(ext) !== -1;

        var li = document.createElement("li");
        li.className = "dropzone__file" + (ok ? "" : " is-rejected");

        var meta = document.createElement("div");
        meta.className = "dropzone__file-meta";

        var name = document.createElement("span");
        name.className = "dropzone__file-name";
        name.textContent = file.name;

        var sub = document.createElement("span");
        sub.className = "dropzone__file-sub";
        sub.textContent = ok
          ? formatSize(file.size)
          : formatSize(file.size) + " · " + (ext ? "." + ext : "?") + " — unsupported";

        meta.appendChild(name);
        meta.appendChild(sub);

        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "dropzone__file-remove";
        remove.setAttribute("aria-label", "Remove");
        remove.textContent = "×";
        remove.addEventListener("click", function () {
          staged.delete(key);
          render();
        });

        li.appendChild(meta);
        li.appendChild(remove);
        filesEl.appendChild(li);
      });

      countEl.textContent = String(staged.size);
      list.hidden = staged.size === 0;
    }

    function addFiles(fileList) {
      Array.prototype.forEach.call(fileList, function (file) {
        staged.set(keyFor(file), file);
      });
      render();
    }

    area.addEventListener("click", function () {
      input.click();
    });
    area.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });

    input.addEventListener("change", function () {
      if (input.files && input.files.length) addFiles(input.files);
      input.value = ""; // allow re-selecting the same file
    });

    ["dragenter", "dragover"].forEach(function (evt) {
      area.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        area.classList.add("is-dragover");
      });
    });
    ["dragleave", "dragend", "drop"].forEach(function (evt) {
      area.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        area.classList.remove("is-dragover");
      });
    });
    area.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        addFiles(e.dataTransfer.files);
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        staged.clear();
        render();
      });
    }

    function acceptedFiles() {
      var out = [];
      staged.forEach(function (file) {
        if (ACCEPT_EXT.indexOf(extOf(file.name)) !== -1) out.push(file);
      });
      return out;
    }

    function setStatus(text, isError) {
      if (!statusEl) return;
      statusEl.textContent = text;
      statusEl.classList.toggle("is-error", !!isError);
    }

    if (processBtn && uploadUrl) {
      processBtn.addEventListener("click", function () {
        var toSend = acceptedFiles();
        if (!toSend.length) {
          setStatus("No supported files to upload.", true);
          return;
        }

        var form = new FormData();
        toSend.forEach(function (file) {
          form.append("files", file, file.name);
        });
        if (driverName) form.append("driver_name", driverName);

        processBtn.disabled = true;
        setStatus("Uploading " + toSend.length + " file(s)…", false);

        var headers = {};
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) headers["X-CSRFToken"] = meta.content;

        fetch(uploadUrl, {
          method: "POST",
          body: form,
          headers: headers,
          credentials: "same-origin",
        })
          .then(function (resp) {
            return resp.json().then(function (data) {
              return { ok: resp.ok, data: data };
            });
          })
          .then(function (res) {
            if (!res.ok) {
              setStatus("Upload failed.", true);
              return;
            }
            var savedCount = res.data.saved_count || 0;
            var rejectedCount = res.data.rejected_count || 0;
            var msg = "Uploaded " + savedCount + " file(s) to the inbox.";
            if (rejectedCount) msg += " " + rejectedCount + " skipped.";
            setStatus(msg, false);
            staged.clear();
            render();
            // Surface the uploaded files. In refresh mode, tell an inline
            // "files awaiting recognition" list to reload; otherwise navigate
            // to the inbox page.
            if (savedCount > 0) {
              if (zone.dataset.inboxRefresh) {
                document.body.dispatchEvent(new Event("inboxChanged"));
              } else if (inboxUrl) {
                window.location.assign(inboxUrl);
              }
            }
          })
          .catch(function () {
            setStatus("Upload failed — network error.", true);
          })
          .finally(function () {
            processBtn.disabled = false;
          });
      });
    }
  }

  function initAll(root) {
    (root || document)
      .querySelectorAll("[data-dropzone]")
      .forEach(initZone);
  }

  if (document.readyState !== "loading") {
    initAll(document);
  } else {
    document.addEventListener("DOMContentLoaded", function () {
      initAll(document);
    });
  }

  // Re-wire zones inserted via htmx swaps.
  document.body.addEventListener("htmx:afterSwap", function (e) {
    initAll(e.target);
  });
})();
