// Marks the column for "now" on any court grid (table.cgrid with data-start on its header cells),
// and scrolls that column into view. Purely visual: nothing is sent to the server.
(function () {
  document.querySelectorAll("table.cgrid").forEach(function (table) {
    var heads = table.querySelectorAll("thead th[data-start]");
    var now = Date.now(), index = -1;
    heads.forEach(function (th, i) {
      var start = new Date(th.dataset.start).getTime();
      if (start <= now && now < start + 30 * 60 * 1000) index = i;
    });
    if (index < 0) return;
    table.querySelectorAll("tr").forEach(function (tr) {
      var cell = tr.querySelectorAll("th:not(.court-col), td")[index];
      if (cell) cell.classList.add("is-now");
    });
    var wrap = table.closest(".grid-wrap");
    if (wrap) wrap.scrollLeft = Math.max(0, heads[index].offsetLeft - 220);
  });
})();
