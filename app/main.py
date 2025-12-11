"""
Main FastAPI application.
Entry point for the Credit Card Statement Analyzer.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.database import init_db
from app.routers import upload, statements, ml


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    """
    # Startup: Initialize database
    await init_db()
    print(f"✓ Database initialized: {settings.database_url}")
    print(f"✓ Upload directory: {settings.upload_dir}")
    print(f"✓ Server starting on {settings.host}:{settings.port}")

    yield

    # Shutdown
    print("✓ Server shutting down")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="Parse credit card statements, extract transactions, and analyze spending patterns",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

# Include routers
app.include_router(upload.router)
app.include_router(statements.router)
app.include_router(ml.router)


# Web routes (HTML pages)
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with file upload form."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "title": "Upload Statement"}
    )


@app.get("/statements", response_class=HTMLResponse)
async def statements_page(request: Request):
    """Statements list page."""
    return templates.TemplateResponse(
        "statement_list.html",
        {"request": request, "title": "Statements"}
    )


@app.get("/statements/{statement_id}", response_class=HTMLResponse)
async def statement_detail_page(request: Request, statement_id: int):
    """Statement detail page."""
    return templates.TemplateResponse(
        "statement_detail.html",
        {
            "request": request,
            "title": "Statement Detail",
            "statement_id": statement_id
        }
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Analytics dashboard page."""
    return templates.TemplateResponse(
        "analytics_dashboard.html",
        {"request": request, "title": "Analytics Dashboard"}
    )


@app.get("/preview", response_class=HTMLResponse)
async def preview_page(request: Request):
    """Preview statement data before saving."""
    return templates.TemplateResponse(
        "preview.html",
        {"request": request, "title": "Preview Statement"}
    )


@app.get("/transactions", response_class=HTMLResponse)
async def all_transactions_page(request: Request):
    """All transactions search page."""
    return templates.TemplateResponse(
        "all_transactions.html",
        {"request": request, "title": "All Transactions"}
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
