import asyncio
import hmac
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.config import Settings


async def probe_dependencies(settings: Settings) -> dict[str, bool]:
    async def check_database() -> bool:
        connection = await asyncpg.connect(settings.database_url, timeout=2)
        try:
            await connection.execute("SELECT 1")
            return True
        finally:
            await connection.close()

    async def check_redis() -> bool:
        client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            return bool(await asyncio.wait_for(client.ping(), timeout=2))
        finally:
            await client.close()

    checks = await asyncio.gather(
        asyncio.wait_for(check_database(), timeout=3),
        asyncio.wait_for(check_redis(), timeout=3),
        return_exceptions=True,
    )
    return {
        "database": checks[0] is True,
        "redis": checks[1] is True,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    configured.validate()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configured.validate()
        yield

    production = configured.environment == "production"
    app = FastAPI(
        title="AWE Enterprise Platform",
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            token = configured.api_token
            authorization = request.headers.get("authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if not token:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "API authentication is not configured"},
                )
            if (
                scheme.lower() != "bearer"
                or not supplied
                or not hmac.compare_digest(supplied, token)
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.get("/health/live", tags=["health"])
    async def liveness():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def readiness():
        if not configured.database_url or not configured.redis_url:
            checks = {"database": False, "redis": False}
        else:
            checks = await probe_dependencies(configured)
        ready = all(checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @app.get("/api/status", tags=["status"])
    async def api_status():
        return {"status": "running", "environment": configured.environment}

    return app


app = create_app()


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
