import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.clients import router as clients_router
from app.routes.refinement import router as refinement_router
from app.routes.requests import router as requests_router
from app.routes.test_extract import router as test_router
from app.routes.users import router as users_router
from app.routes.voucher import router as voucher_router
from app.routes.workspace import router as workspace_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm PDF rendering and start durable extraction/indexing workers."""
    from app.services.extraction_worker import extraction_worker_loop
    from app.services.indexing_worker import indexing_worker_loop
    from app.services.pdf_service import render_html_pdf

    try:
        await asyncio.to_thread(render_html_pdf, "<html><body></body></html>")
    except Exception as exc:
        logger.warning("PDF renderer warmup failed (%s)", type(exc).__name__)
    indexing_task = asyncio.create_task(indexing_worker_loop())
    worker_task = asyncio.create_task(extraction_worker_loop())
    logger.info("Extraction worker task created.")
    try:
        yield
    finally:
        indexing_task.cancel()
        await asyncio.gather(indexing_task, return_exceptions=True)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Extraction worker task stopped.")


app = FastAPI(
    title="AMIGA Robot Admin",
    description="Intelligent Vendor Invoice-to-Voucher Processing",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routes.operations import router as operations_router

app.include_router(operations_router)
app.include_router(chat_router)
app.include_router(workspace_router)
app.include_router(refinement_router)
app.include_router(auth_router)
app.include_router(clients_router)
app.include_router(users_router)
app.include_router(requests_router)
app.include_router(test_router)
app.include_router(admin_router)
app.include_router(voucher_router)


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "workflow": "organization-v4"}
