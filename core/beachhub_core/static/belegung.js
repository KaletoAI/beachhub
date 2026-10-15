// Belegungsplan: Feldauswahl wirkt sofort, ohne den (immer sichtbaren) "Anzeigen"-Button zu
// erfordern. Rein progressive Verbesserung – ohne JavaScript funktioniert das Formular
// unverändert über den Button.
document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("belegung-feld-form");
  if (!form) {
    return;
  }
  form.querySelectorAll("input[name='feld']").forEach(function (haken) {
    haken.addEventListener("change", function () {
      form.submit();
    });
  });
});
