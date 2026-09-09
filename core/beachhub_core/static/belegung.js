// Belegungsplan: bequemer Feldwechsel ohne den (immer sichtbaren) "Anzeigen"-Button zu
// erfordern. Rein progressive Verbesserung – ohne JavaScript funktioniert das Formular
// unverändert über den Button.
document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("belegung-feld-form");
  var auswahl = form ? form.querySelector("select[name='feld']") : null;
  if (auswahl) {
    auswahl.addEventListener("change", function () {
      form.submit();
    });
  }
});
