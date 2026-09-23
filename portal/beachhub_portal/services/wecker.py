"""Weckt wartende Long-Polls, sobald eine neue Anfrage angelegt wurde.

Ein Zähler statt asyncio.Event: Anfragen entstehen in synchronen Routen (Threadpool), der
Long-Poll wartet im Event-Loop. Ein Event wäre an einen Loop gebunden; der Zähler ist über
Threads hinweg sicher, und der Long-Poll vergleicht ihn alle 50 ms. Anfragen aus einem anderen
Worker sieht er nicht – dafür prüft der Long-Poll zusätzlich jede Sekunde die Datenbank.
"""

import threading

_sperre = threading.Lock()
_stand = 0


def wecke() -> None:
    global _stand
    with _sperre:
        _stand += 1


def stand() -> int:
    return _stand
