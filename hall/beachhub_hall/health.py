"""GET /health für Docker und Monitoring. Keine weitere Oberfläche: Status und Handbetrieb
gibt es im HA-Dashboard."""

from aiohttp import web

from beachhub_hall.dienst import Dienst


def health_app(dienst: Dienst) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        status = dienst.status()
        return web.json_response(
            {
                "status": "ok",
                "planversion": status.planversion,
                "ha_verbunden": status.ha_erreichbar,
                "warteschlange": status.warteschlange,
            }
        )

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def starte_health(dienst: Dienst, host: str, port: int) -> web.AppRunner:
    """`host` kommt aus der Einstellung `HEALTH_HOST` (Vorgabe 127.0.0.1) – im Container kann
    der Betreiber sie auf 0.0.0.0 setzen, ohne dass der Dienst standardmäßig auf allen
    Schnittstellen lauscht."""
    runner = web.AppRunner(health_app(dienst))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
