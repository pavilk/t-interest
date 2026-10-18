from fastapi import APIRouter, FastAPI

from app.api import auth, matching, me, survey
from app.api.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Tinterest API", version="0.1.0")
    register_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(me.router)
    api.include_router(survey.router)
    api.include_router(matching.router)
    # TODO: groups, chats, search — подключать сюда же
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
