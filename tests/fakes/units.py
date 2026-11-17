"""ChatUnits на фейках для WS-тестов: та же семантика, что у ChatUnitFactory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import Settings
from app.core.db import EventPublisher, Outbox
from app.core.rate_limit import RateLimiter
from app.services.chat import ChatService
from app.services.identity import AuthenticatedUser, IdentityService
from tests.fakes.repositories import FakeChatRepository, FakeOfficeRepository, FakeUserRepository


class FakeChatUnits:
    def __init__(
        self,
        users: FakeUserRepository,
        offices: FakeOfficeRepository,
        chats: FakeChatRepository,
        publisher: EventPublisher,
        limiter: RateLimiter,
        settings: Settings,
    ) -> None:
        self._identity = IdentityService(users, offices, settings)
        self._chats = chats
        self._publisher = publisher
        self._limiter = limiter

    async def authenticate(self, access_token: str) -> AuthenticatedUser:
        return await self._identity.authenticate_for_chat(access_token)

    @asynccontextmanager
    async def chat_service(self) -> AsyncIterator[ChatService]:
        # как session_scope: события уходят только при успешном выходе из блока
        outbox = Outbox()
        yield ChatService(self._chats, outbox, self._limiter)
        for event in outbox.events:
            self._publisher.publish(event)
