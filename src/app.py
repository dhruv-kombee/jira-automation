import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import config, validate_config
from src.database import init_database, close_database
from src.logger import logger
from src.routes.health import router as health_router
from src.routes.webhooks import router as webhooks_router
from src.routes.dashboard import router as dashboard_router
from src.tunnel import ensure_tunnel
from src.services.subscription_manager import (
    ensure_subscription_online,
    start_auto_renew,
    stop_auto_renew,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup validation
    missing = validate_config()
    if missing:
        logger.warning(
            f"Missing configuration: {', '.join(missing)}. Some features will not work.",
            extra={"event": "AUTHENTICATION_ERROR", "missing": missing},
        )

    # 2. Database
    init_database()

    # 3. Tunnel detection / startup
    tunnel_url = ensure_tunnel(port=config.port)
    if tunnel_url:
        logger.info(f"Public webhook tunnel ready: {tunnel_url}", extra={"event": "TUNNEL_READY", "url": tunnel_url})
        # 4. Ensure Microsoft Graph subscription is online and points to active tunnel
        sub_res = ensure_subscription_online(public_url=tunnel_url)
        logger.info(f"Graph subscription status: {sub_res.get('status')}", extra={"event": "SUBSCRIPTION_STATUS", "details": sub_res})
    else:
        logger.warning("No tunnel available on startup. Run ngrok or set WEBHOOK_PUBLIC_URL.", extra={"event": "TUNNEL_WARNING"})

    # 5. Start background auto-renewal loop
    start_auto_renew()

    logger.info(
        f"Teams to Jira Automation Hub online at http://localhost:{config.port}",
        extra={
            "event": "SERVER_START",
            "port": config.port,
            "dashboard": f"http://localhost:{config.port}/",
            "tunnel": tunnel_url,
        },
    )

    yield

    # Shutdown
    stop_auto_renew()
    from src.tunnel import stop_tunnel
    stop_tunnel()
    close_database()


app = FastAPI(
    title="Jira-Automation Teams Backend",
    description="Teams → Jira automation system — MVP Phase 1: Teams message detection via Microsoft Graph",
    version="0.1.0",
    lifespan=lifespan,
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    # Don't flood logs with static files or status polling
    if not request.url.path.startswith("/static") and request.url.path != "/api/status":
        logger.debug(f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms")
    return response


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled error: {exc}",
        extra={
            "event": "GRAPH_API_ERROR",
            "error": str(exc),
            "path": request.url.path,
        },
        exc_info=True,
    )
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


from src.routes.admin import router as admin_router

# Serve Dashboard at root and /dashboard
@app.get("/", response_class=FileResponse)
@app.get("/dashboard", response_class=FileResponse)
def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    return FileResponse(str(index_file))


# Serve Admin / Settings Management Hub at /settings and /admin
@app.get("/settings", response_class=FileResponse)
@app.get("/admin", response_class=FileResponse)
def serve_settings():
    settings_file = STATIC_DIR / "settings.html"
    return FileResponse(str(settings_file))


# Include routers
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(dashboard_router)
app.include_router(admin_router)

