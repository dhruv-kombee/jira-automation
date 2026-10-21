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

    # 3. Ingress detection / startup (Cloud public URL or local ngrok tunnel)
    public_url = config.webhook_public_url or ensure_tunnel(port=config.port)
    if public_url:
        logger.info(f"Public webhook URL ready: {public_url}", extra={"event": "PUBLIC_URL_READY", "url": public_url})
        # 4. Schedule automatic subscription verification once server port binds
        async def _startup_subscription_init():
            import asyncio
            import socket
            connected = False
            for _ in range(30):
                try:
                    with socket.create_connection(("127.0.0.1", config.port), timeout=0.2):
                        connected = True
                        break
                except (ConnectionRefusedError, OSError):
                    await asyncio.sleep(0.2)

            if not connected:
                logger.warning(f"Server did not start listening on port {config.port} within timeout.")
                return

            active_url = config.webhook_public_url or public_url or ensure_tunnel(port=config.port)
            if active_url:
                logger.info("Server port ready. Establishing Microsoft Graph subscription...")
                sub_res = await asyncio.to_thread(ensure_subscription_online, active_url)
                status = sub_res.get("status")
                logger.info(f"Graph subscription status: {status}", extra={"event": "SUBSCRIPTION_STATUS", "details": sub_res})
                if status in ("created", "renewed", "active"):
                    sub_data = sub_res.get("subscription") or {}
                    sub_id = sub_data.get("id") or "active"
                    exp = sub_data.get("expirationDateTime") or "active"
                    print(f"\n[+] Microsoft Graph Subscription Online: {sub_id} (Expires: {exp})\n")
            else:
                logger.warning("Subscription initialization deferred: no active public webhook URL.")

        import asyncio
        asyncio.create_task(_startup_subscription_init())
    else:
        logger.warning("No tunnel or WEBHOOK_PUBLIC_URL available on startup. Run ngrok or set WEBHOOK_PUBLIC_URL.", extra={"event": "TUNNEL_WARNING"})

    # 5. Start background auto-renewal loop
    start_auto_renew()

    # 6. Start PM Follow-up SLA reminder monitor
    from src.services.reminder_service import start_reminder_monitor, stop_reminder_monitor
    start_reminder_monitor()

    # 7. Initial Teams roster check & sync with Member.xlsx, start roster monitor and file watcher
    try:
        from src.services.member_sync_service import (
            sync_from_onedrive_sheet,
            sync_teams_chat_roster,
            start_roster_monitor,
            stop_roster_monitor,
            start_excel_watcher,
            stop_excel_watcher,
        )
        # Seed local cache from live OneDrive Cloud URL first (vital for headless / container environments)
        sync_from_onedrive_sheet()
        sync_teams_chat_roster()
        start_roster_monitor()
        start_excel_watcher()
    except Exception as roster_err:
        logger.debug(f"Initial roster sync notice: {roster_err}")

    logger.info(
        f"Teams to Jira Automation Hub online at http://localhost:{config.port}",
        extra={
            "event": "SERVER_START",
            "port": config.port,
            "mode": "headless",
            "public_url": public_url,
        },
    )

    yield

    # Shutdown
    stop_auto_renew()
    stop_reminder_monitor()
    try:
        from src.services.member_sync_service import stop_roster_monitor, stop_excel_watcher
        stop_roster_monitor()
        stop_excel_watcher()
    except Exception:
        pass
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


from src.routes.admin import router as admin_router
from src.routes.auth import router as auth_router
from src.routes.approvals import router as approvals_router

# Headless root status info
@app.get("/")
def root_info():
    return {
        "status": "online",
        "service": "JiraAutomation Teams Backend",
        "mode": "headless",
        "version": "0.1.0",
        "message": "Teams to Jira Automation Hub is running in headless service mode.",
    }


# Dashboard disabled endpoint
@app.get("/dashboard")
def dashboard_disabled():
    return JSONResponse(
        status_code=404,
        content={"error": "Dashboard has been disabled. The service operates headlessly."},
    )


# Settings / Admin web console disabled (managed via Member.xlsx)
@app.get("/settings")
@app.get("/admin")
def settings_disabled():
    return JSONResponse(
        status_code=404,
        content={"message": "Web settings console disabled. Member details and roles are managed directly via Member.xlsx."},
    )


# Include routers
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(approvals_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(auth_router)

