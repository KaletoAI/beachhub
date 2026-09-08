from datetime import date, time

from beachhub_shared.slots import (
    Ausnahme,
    Betriebszeit,
    RasterKonfig,
    slots_fuer_tag,
    slots_im_zeitraum,
    zeitraum_ist_slotfolge,
)
from beachhub_shared.zeit import kombiniere

MI = date(2027, 12, 1)  # Mittwoch, weekday 2
BZ = [Betriebszeit(wochentag=2, oeffnet=time(17, 0), schliesst=time(23, 0))]


def test_dauer_raster_erzeugt_stundenslots() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert len(s) == 6
    assert s[0].beginn == kombiniere(MI, time(17, 0))
    assert s[-1].ende == kombiniere(MI, time(23, 0))


def test_dauer_raster_90_minuten_laesst_rest_weg() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=90, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert len(s) == 4  # 17:00-18:30, 18:30-20:00, 20:00-21:30, 21:30-23:00


def test_fenster_raster_nur_innerhalb_betriebszeit() -> None:
    raster = [
        RasterKonfig(
            wochentag=None,
            modus="fenster",
            slot_minuten=None,
            fenster=[
                (time(19, 0), time(21, 0)),
                (time(21, 0), time(23, 0)),
                (time(23, 0), time(1, 0)),
            ],
        )
    ]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert [(x.beginn.hour, x.ende.hour) for x in s] == [(18, 20), (20, 22)]  # UTC-Stunden


def test_wochentag_raster_gewinnt() -> None:
    raster = [
        RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[]),
        RasterKonfig(wochentag=2, modus="dauer", slot_minuten=120, fenster=[]),
    ]
    assert len(slots_fuer_tag(MI, raster, BZ, [])) == 3


def test_ausnahme_geschlossen() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    aus = [Ausnahme(datum=MI, geschlossen=True, oeffnet=None, schliesst=None)]
    assert slots_fuer_tag(MI, raster, BZ, aus) == []


def test_ausnahme_sonderzeiten() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    aus = [Ausnahme(datum=MI, geschlossen=False, oeffnet=time(10, 0), schliesst=time(12, 0))]
    assert len(slots_fuer_tag(MI, raster, BZ, aus)) == 2


def test_ohne_betriebszeit_keine_slots() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    assert slots_fuer_tag(date(2027, 12, 2), raster, BZ, []) == []


def test_slotfolge_pruefung() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert zeitraum_ist_slotfolge(kombiniere(MI, time(19, 0)), kombiniere(MI, time(21, 0)), s)
    assert not zeitraum_ist_slotfolge(kombiniere(MI, time(19, 30)), kombiniere(MI, time(21, 0)), s)
    assert not zeitraum_ist_slotfolge(kombiniere(MI, time(22, 0)), kombiniere(MI, time(23, 30)), s)
    assert len(slots_im_zeitraum(kombiniere(MI, time(19, 0)), kombiniere(MI, time(21, 0)), s)) == 2
