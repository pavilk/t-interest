from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chats, me, ws
from app.api.connections import ConnectionManager
from app.api.errors import register_error_handlers
from app.core.config import CorsSettings


def create_app() -> FastAPI:
    app = FastAPI(title="Tinterest API", version="0.1.0")
    register_error_handlers(app)
    origins = CorsSettings().origins
    if origins:
        # cookie не используются: токен идёт в заголовке Authorization
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
        )
    # один менеджер на процесс: события из REST и WS доходят до всех соединений
    app.state.connections = ConnectionManager()
    app.state.event_publisher = app.state.connections
    ws.add_ws_schemas(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(me.router)
    api.include_router(chats.router)
    api.include_router(ws.router)
    # TODO: survey, matching, groups, search — подключать сюда же
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
