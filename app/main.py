"""
Main FastAPI application — Personal Finance Intelligence Platform.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import init_db
from app.routers import upload, statements, ml
from app.routers import accounts, categories, advisor, budgets, reports
from app.routers import daily_expenses, daily_income, liabilities
from app.routers import auth as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    """
    # 1. Initialize database (Alembic migrations + create_all fallback)
    await init_db()
    print(f"✓ Database initialized: {settings.database_url}")

    # 2. Seed financial institutions (idempotent)
    try:
        from app.database import AsyncSessionLocal
        from app.services.seed_data import seed_institutions
        async with AsyncSessionLocal() as db:
            await seed_institutions(db)
        print("✓ Financial institutions seeded")
    except Exception as e:
        print(f"⚠ Institution seeding failed: {e}")

    # 3. Seed category rules (idempotent)
    try:
        from app.database import AsyncSessionLocal
        from app.services.category_engine import seed_category_rules
        async with AsyncSessionLocal() as db:
            await seed_category_rules(db)
        print("✓ Category rules seeded")
    except Exception as e:
        print(f"⚠ Category rule seeding failed: {e}")

    # 4. Start background scheduler
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
        print("✓ Background scheduler started")
    except Exception as e:
        print(f"⚠ Scheduler failed to start: {e}")

    print(f"✓ Upload directory: {settings.upload_dir}")
    print(f"✓ Claude Vision: {'enabled' if settings.anthropic_api_key else 'disabled (set ANTHROPIC_API_KEY)'}")
    print(f"✓ Server starting on {settings.host}:{settings.port}")

    yield

    # Shutdown
    try:
        from app.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    print("✓ Server shutting down")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    description=(
        "AI-powered personal finance platform: "
        "parse multi-bank statements, track spending across cards, "
        "and get personalized financial insights."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)

# Session middleware — required by Authlib for Google OAuth PKCE state
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=not settings.debug,
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = settings.app_version

# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
app.include_router(auth_router.router)   # /api/auth/*  (public)
app.include_router(upload.router)
app.include_router(statements.router)
app.include_router(ml.router)
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(advisor.router)
app.include_router(budgets.router)
app.include_router(reports.router)
app.include_router(daily_expenses.router)
app.include_router(daily_income.router)
app.include_router(liabilities.router)


# ---------------------------------------------------------------------------
# Internal helper — resolve the current user from cookie (no Depends overhead)
# ---------------------------------------------------------------------------

async def _resolve_user(request: Request):
    """Decode the JWT cookie and return the User ORM object, or None."""
    from app.database import AsyncSessionLocal
    from app.models import User
    from app.utils.auth import decode_token
    from sqlalchemy import select
    try:
        token = request.cookies.get("access_token")
        if not token:
            return None
        payload = decode_token(token)
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(User).where(User.id == int(payload["sub"])))
            user = r.scalars().first()
            return user if (user and user.is_active) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public HTML pages (no auth required)
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"title": "Sign In"})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"title": "Create Account"})


# ---------------------------------------------------------------------------
# Protected HTML routes — redirect to /login if not authenticated
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "index.html", {
        "title": "Upload Statement", "current_user": user
    })


@app.get("/statements", response_class=HTMLResponse)
async def statements_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "statement_list.html", {
        "title": "Statements", "current_user": user
    })


@app.get("/statements/{statement_id}", response_class=HTMLResponse)
async def statement_detail_page(request: Request, statement_id: int):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "statement_detail.html", {
        "title": "Statement Detail",
        "statement_id": statement_id,
        "current_user": user,
    })


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "reports.html", {
        "title": "Reports", "current_user": user
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "dashboard.html", {
        "title": "Dashboard", "current_user": user
    })


@app.get("/preview", response_class=HTMLResponse)
async def preview_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "preview.html", {
        "title": "Preview Statement", "current_user": user
    })


@app.get("/transactions", response_class=HTMLResponse)
async def all_transactions_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "all_transactions.html", {
        "title": "All Transactions", "current_user": user
    })


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "accounts.html", {
        "title": "My Cards & Accounts", "current_user": user
    })


@app.get("/advisor", response_class=HTMLResponse)
async def advisor_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "advisor.html", {
        "title": "AI Advisor", "current_user": user
    })


@app.get("/daily-expenses", response_class=HTMLResponse)
async def daily_expenses_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "daily_expenses.html", {
        "title": "Daily Expenses", "current_user": user
    })


@app.get("/daily-income", response_class=HTMLResponse)
async def daily_income_page(request: Request):
    user = await _resolve_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "daily_income.html", {
        "title": "Income Tracker", "current_user": user
    })


# ---------------------------------------------------------------------------
# Health check (public)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "claude_vision": bool(settings.anthropic_api_key),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
