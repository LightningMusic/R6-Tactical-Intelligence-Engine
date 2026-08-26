from contextlib import asynccontextmanager
from fastapi import FastAPI
from server.config import server_settings
from server.api.v1 import router as v1_router
from server.worker import server_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    server_settings.ensure_directories()
    server_worker.start_in_background()
    yield
    # Shutdown actions
    server_worker.stop()


def create_app() -> FastAPI:
    """
    Creates and configures the headless FastAPI remote server application.
    Independent of PySide6, Qt, GUI, OBS, Discord, and client settings.
    """
    app = FastAPI(
        title="R6Analyzer Remote Server",
        description="Headless archival database and async analysis server for R6Analyzer",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
