"""Адаптер: браузерная сессия живёт в хранилище аккаунтов `eazy-sdk`.

Вход в сервис — самая дорогая часть сценария, и хранить его результат у каждого SDK
по-своему (файл рядом с кодом, свой формат, свои имена) — это писать одно и то же в
третий раз. У `eazy-sdk` для этого уже есть хранилище: аккаунт, его сессии, история
событий, ограничения и пул с ротацией. Браузеру от него нужно то же, что и HTTP.

    workspace = AccountWorkspace(storage)
    sessions = BrowserSessions(workspace)
    account = await workspace.accounts.get_or_create("user@example.com", provider="mail")

    # Сессия контекста по жизненному циклу ядра: хранится за аккаунтом, ревизия — в `meta`.
    mail = MAIL_LOGIN.session(credentials, store=sessions.store(account))

    # Или руками: состояние для нового контекста и сохранение готового.
    saved = await sessions.load(account)
    context = await browser.new_context(storage_state=to_storage_state(saved))
    await sessions.save(account, state)

Браузера здесь нет: когда положить сессию в контекст, решает тот, кто его создаёт
(`storage_state`), или `BrowserSession` (куки). Интеграция хранилища только хранит.

Нужен именно `AccountWorkspace` — со службами `sessions`, `history`, `restrictions`,
`pool`. `open_workspace` из `eazy_sdk_sqlmodel` отдаёт `SqlAccountWorkspace` с другим
набором служб, и сюда он не подходит.

Где проходит шов. `SessionData.cookies` — это `dict[str, str]`, пара «имя-значение»:
её достаточно HTTP-клиенту, который шлёт заголовок `Cookie` на известный ему адрес, и
мало браузеру — тот не примет куку без `domain`, `path` и срока. Поэтому полное
состояние кладётся в `SessionData.params`, а плоский словарь остаётся **проекцией**
для тех, кому хватает пары. Это обходной путь, а не замысел: правильным решением было
бы дать `SessionData` настоящую модель куки — `eazy_sdk.cookies.StoredCookie`, с областью
действия, путём, флагами и сроком.

Зависимость необязательная: ядро о хранилище не знает, ставится экстрой
`eazy-browser[accounts]`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from eazy_sdk_accounts.storage import (
    AccountRecord,
    AccountWorkspace,
    RepositorySessionStore,
    SessionData,
)
from eazy_sdk_accounts.storage.entities import Account

from eazy_sdk.cookies import StoredCookie
from eazy_sdk_browser.state import BrowserState, Origin

if TYPE_CHECKING:
    from eazy_sdk.auth.session import SessionStore

BROWSER_SCHEME = "browser"
"""Чем записана сессия. У HTTP-схем (`bearer`, `cookie`) на этом месте стоит свой способ."""

STATE_FIELD = "browser_state"
"""Ключ в `SessionData.params`, под которым лежит состояние целиком."""

REVISION_FIELD = "revision"
"""Ключ в `SessionData.meta`: ревизия сессии жизненного цикла ядра."""

KEY_FIELD = "session_key"
"""Ключ в `SessionData.meta`: чья это сессия в терминах жизненного цикла (`browser:<кто>`)."""


def to_session_data(
    state: BrowserState,
    *,
    expires_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> SessionData:
    """Состояние браузера в терминах хранилища.

    `expires_at` не угадывается по кукам: у каждой свой срок, и самый ранний из них
    обычно принадлежит не той, что держит вход. Срок знает тот, кто знает сервис.
    """
    return SessionData(
        scheme=BROWSER_SCHEME,
        cookies={cookie.name: cookie.value for cookie in state.cookies},
        params={STATE_FIELD: _dump(state)},
        meta=meta or {},
        expires_at=expires_at,
    )


def from_session_data(data: SessionData, *, domain: str | None = None) -> BrowserState:
    """Состояние браузера из записи хранилища.

    Записи, сделанной браузером, хватает целиком. У записи от HTTP-SDK есть только
    плоские куки — их можно применить, если вызывающий скажет, к какому домену они
    относятся; без этого браузеру их девать некуда.
    """
    stored = data.params.get(STATE_FIELD)
    if isinstance(stored, dict):
        return _load(stored)
    if domain is None:
        return BrowserState()
    # Плоская кука не знает своей области: домен сайта значит «он и его поддомены».
    site = domain.lstrip(".").lower()
    return BrowserState(
        cookies=tuple(
            StoredCookie(name=name, value=value, domain=site, host_only=False)
            for name, value in data.cookies.items()
        )
    )


@dataclass(frozen=True, slots=True)
class BrowserSessions[AccountT: Account[Any] = AccountRecord]:
    """Сессии браузера поверх хранилища аккаунтов.

    Тонкая обёртка: всё, кроме перевода состояния в запись и обратно, делает сам
    `AccountWorkspace`. Появись у него браузерные понятия — это был бы уже не общий
    слой, а его ветка для браузера.
    """

    workspace: AccountWorkspace[AccountT]
    label: str = BROWSER_SCHEME
    domain: str | None = None
    """Домен для плоских кук из чужой (не браузерной) записи."""

    async def save(
        self, account: AccountT, state: BrowserState, *, expires_at: datetime | None = None
    ) -> None:
        """Сохранить готовое состояние за аккаунтом. Откуда оно — не забота хранилища."""
        await self.workspace.sessions.save(
            account, to_session_data(state, expires_at=expires_at), label=self.label
        )

    async def load(self, account: AccountT) -> BrowserState | None:
        """Сохранённое состояние аккаунта, если оно есть и ещё не протухло."""
        session = await self.workspace.sessions.active(account, label=self.label)
        if session is None:
            return None
        expires_at = session.expires_at
        if expires_at is not None and expires_at <= datetime.now(expires_at.tzinfo):
            return None
        return from_session_data(session.to_session_data(), domain=self.domain)

    async def forget(self, account: AccountT) -> None:
        """Сессия больше не годится: сервис увёл на форму входа."""
        await self.workspace.sessions.invalidate(account, label=self.label)

    def store(self, account: AccountT) -> SessionStore[BrowserState]:
        """Хранилище сессии аккаунта для `BrowserLogin.session` — готовый `RepositorySessionStore`.

        Своего хранилища здесь нет: `eazy_sdk_accounts` уже сшивает жизненный цикл ядра с
        репозиторием, плагину остаётся дать репозиторий одного аккаунта и кодек состояния.
        """
        return RepositorySessionStore(
            _AccountSessionData(self.workspace, account, self.label),
            BrowserStateCodec(self.domain),
        )


@dataclass(frozen=True, slots=True)
class BrowserStateCodec:
    """`SessionCodec[BrowserState]` хранилища: состояние — в `SessionData` и обратно."""

    domain: str | None = None
    """Домен для плоских кук из чужой (не браузерной) записи."""

    def encode(self, value: BrowserState) -> object:
        return to_session_data(value)

    def decode(self, value: object) -> BrowserState:
        if not isinstance(value, SessionData):
            msg = f"stored browser session must be SessionData, got {type(value).__name__}"
            raise TypeError(msg)
        return from_session_data(value, domain=self.domain)


@dataclass(frozen=True, slots=True)
class _AccountSessionData[AccountT: Account[Any]]:
    """`SessionDataRepository` одного аккаунта: сессия под меткой, ревизия и ключ — в `meta`.

    Ключ проверяется при чтении: сессия, записанная под другим ключом (другой вход того же
    аккаунта), этому входу не отдаётся.
    """

    workspace: AccountWorkspace[AccountT]
    account: AccountT
    label: str

    async def load_session_data(self, key: str) -> tuple[object, int] | None:
        session = await self.workspace.sessions.active(self.account, label=self.label)
        if session is None:
            return None
        data = session.to_session_data()
        if data.meta.get(KEY_FIELD, key) != key:
            return None
        return data, int(data.meta.get(REVISION_FIELD, 0))

    async def save_session_data(self, key: str, value: object, revision: int) -> None:
        if not isinstance(value, SessionData):
            msg = f"browser session is saved as SessionData, got {type(value).__name__}"
            raise TypeError(msg)
        meta = {**value.meta, KEY_FIELD: key, REVISION_FIELD: revision}
        await self.workspace.sessions.save(
            self.account, replace(value, meta=meta), label=self.label
        )

    async def invalidate_session_data(self, key: str, expected_revision: int | None) -> None:
        current = await self.load_session_data(key)
        if current is None:
            return
        if expected_revision is None or current[1] == expected_revision:
            await self.workspace.sessions.invalidate(self.account, label=self.label)


def _dump(state: BrowserState) -> dict[str, Any]:
    """Состояние в JSON-совместимый вид: хранилище кладёт `params` в колонку JSON."""
    return {
        "cookies": [cookie.to_primitive() for cookie in state.cookies],
        "origins": [
            {"origin": origin.origin, "items": [list(pair) for pair in origin.items]}
            for origin in state.origins
        ],
    }


def _load(stored: dict[str, Any]) -> BrowserState:
    """Состояние из записи. Запись другого формата — это отсутствие сессии, а не ошибка.

    Раньше область действия куки кодировалась точкой в домене. Угадывать её при чтении
    значило бы держать два формата, поэтому такая запись читается как пустое состояние,
    и владелец сессии просто входит заново.
    """
    try:
        cookies = tuple(StoredCookie.from_primitive(raw) for raw in stored.get("cookies", ()))
    except (ValueError, TypeError, AttributeError):
        return BrowserState()
    return BrowserState(
        cookies=cookies,
        origins=tuple(
            Origin(
                origin=raw.get("origin", ""),
                items=tuple((pair[0], pair[1]) for pair in raw.get("items", ())),
            )
            for raw in stored.get("origins", ())
        ),
    )


__all__ = [
    "BROWSER_SCHEME",
    "KEY_FIELD",
    "REVISION_FIELD",
    "STATE_FIELD",
    "BrowserSessions",
    "BrowserStateCodec",
    "from_session_data",
    "to_session_data",
]
