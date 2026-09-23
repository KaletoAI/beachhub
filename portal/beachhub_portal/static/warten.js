// Warteseite: fragt alle 2 s den Stand der Anfrage ab und geht weiter, sobald sich etwas tut.
// Ohne JavaScript übernimmt das <meta http-equiv="refresh"> im <noscript>-Block.
(function () {
  var el = document.getElementById("warten");
  if (!el) return;
  var url = el.getAttribute("data-stand");
  var zustand = el.getAttribute("data-zustand");
  function frage() {
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) { setTimeout(frage, 2000); return; }
        // Nach einer Weiterleitung/einem Neuladen nicht weiter pollen (Ruling Fix-Runde 2):
        // `location.assign` auf ein Attachment (z. B. den Rechnungs-Download) entlädt die Seite
        // nicht, ein `setTimeout` liefe sonst weiter und fragte den – dann schon verbrauchten –
        // Stand erneut ab.
        if (s.ziel) { window.location.assign(s.ziel); return; }
        if (s.zustand !== zustand) { window.location.reload(); return; }
        var text = document.getElementById("warten-text");
        if (text && s.text) text.textContent = s.text;
        setTimeout(frage, 2000);
      })
      .catch(function () { setTimeout(frage, 2000); });
  }
  setTimeout(frage, 2000);
})();
