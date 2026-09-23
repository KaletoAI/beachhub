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


async def starte_health(dienst: Dienst, port: int) -> web.AppRunner:
    runner = web.AppRunner(health_app(dienst))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()  # noqa: S104 – Container-Netz
    return runner
