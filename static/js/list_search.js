// Live search for a page of cards. Usage:
//   <input type="search" data-search=".menu-item" data-empty="no-match">
//   each card: class="menu-item" data-name="what to match (lower case)"
//   optional wrappers with data-search-group are hidden when none of their cards match (e.g. a category).
document.querySelectorAll("input[data-search]").forEach(function (box) {
  var selector = box.dataset.search;
  var cards = document.querySelectorAll(selector);
  var empty = document.getElementById(box.dataset.empty || "");
  box.addEventListener("input", function () {
    var text = box.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card) {
      var match = card.dataset.name.indexOf(text) !== -1;
      card.hidden = !match;
      if (match) shown += 1;
    });
    document.querySelectorAll("[data-search-group]").forEach(function (group) {
      group.hidden = !group.querySelector(selector + ":not([hidden])");
    });
    if (empty) empty.hidden = shown > 0;
  });
});
