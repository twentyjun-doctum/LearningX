"""Request-local timings for the three slide delivery endpoints."""
from time import perf_counter

from fastapi.routing import APIRoute


class LiveTimingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not (self.path.endswith("/live") or self.path.endswith("/live/page/{version}")):
            return handler

        async def timed(request):
            started = perf_counter()
            request.state.live_timing = {}
            response = await handler(request)
            phases = request.state.live_timing
            phases["app"] = (perf_counter() - started) * 1000
            response.headers["Server-Timing"] = ", ".join(
                f"{name};dur={duration:.2f}" for name, duration in phases.items()
            )
            return response

        return timed


def record_phase(request, name, started):
    timings = request.state.live_timing
    timings[name] = timings.get(name, 0) + (perf_counter() - started) * 1000
