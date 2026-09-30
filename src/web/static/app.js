// Margin notes for article citations, and keeping live logs scrolled to the end.
(function () {
  function marginNotes() {
    var article = document.getElementById("article");
    if (!article || article.dataset.done) return;
    article.dataset.done = "1";
    var refs = {};
    try { refs = JSON.parse(article.dataset.cites || "{}"); } catch (e) { return; }
    var placed = {};
    article.querySelectorAll("p").forEach(function (p) {
      var nums = [];
      p.querySelectorAll("a.cite").forEach(function (a) {
        var n = a.dataset.ref;
        if (refs[n] && !placed[n] && nums.indexOf(n) < 0) nums.push(n);
      });
      if (!nums.length) return;
      var side = document.createElement("aside");
      side.className = "side";
      side.setAttribute("aria-hidden", "true");
      nums.forEach(function (n) {
        placed[n] = true;
        var r = refs[n];
        var a = document.createElement("a");
        a.href = "#ref-" + n;
        a.dataset.ref = n;
        a.tabIndex = -1;
        a.innerHTML = '<span class="sn"></span><span class="st"></span><span class="sk"></span>';
        a.querySelector(".sn").textContent = n;
        a.querySelector(".st").textContent = r.title.length > 90 ? r.title.slice(0, 88) + "…" : r.title;
        a.querySelector(".sk").textContent = r.topic;
        side.appendChild(a);
      });
      p.parentNode.insertBefore(side, p);
    });
    // Hovering a citation lights up its margin note, and the other way round.
    article.addEventListener("mouseover", function (e) {
      var t = e.target.closest("[data-ref]");
      if (!t) return;
      article.querySelectorAll('[data-ref="' + t.dataset.ref + '"]').forEach(function (x) { x.classList.add("hot"); });
    });
    article.addEventListener("mouseout", function (e) {
      var t = e.target.closest("[data-ref]");
      if (!t) return;
      article.querySelectorAll(".hot").forEach(function (x) { x.classList.remove("hot"); });
    });
  }

  function scrollLogs(root) {
    (root || document).querySelectorAll("pre[data-autoscroll]").forEach(function (pre) {
      pre.scrollTop = pre.scrollHeight;
    });
  }

  // The job panel is replaced every 2 s; keep whatever the reader opened or closed.
  var openState = {};
  document.addEventListener("click", function (e) {
    var summary = e.target.closest("#job-panel details[id] > summary");
    if (summary) openState[summary.parentNode.id] = !summary.parentNode.open;
  });
  function restoreOpen(root) {
    (root || document).querySelectorAll("#job-panel details[id]").forEach(function (d) {
      if (d.id in openState) d.open = openState[d.id];
    });
  }

  // "Copy prompt" buttons: data-copy points at the element holding the text.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var src = document.querySelector(btn.dataset.copy);
    if (!src) return;
    var text = src.value || src.textContent;
    var done = function () {
      var label = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(function () { btn.textContent = label; btn.classList.remove("copied"); }, 1800);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { legacyCopy(src); done(); });
    } else { legacyCopy(src); done(); }
  });
  function legacyCopy(el) {
    el.removeAttribute("aria-hidden");
    el.select();
    try { document.execCommand("copy"); } catch (err) { /* nothing else to try */ }
    el.setAttribute("aria-hidden", "true");
  }

  document.addEventListener("DOMContentLoaded", function () { marginNotes(); scrollLogs(); });
  document.addEventListener("htmx:afterSettle", function () { restoreOpen(); scrollLogs(); });
  document.addEventListener("htmx:afterSwap", function (e) { scrollLogs(e.target.parentNode || document); });
})();
