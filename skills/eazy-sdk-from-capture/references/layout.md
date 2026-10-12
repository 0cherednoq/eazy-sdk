# Layout of an SDK package

One package, one file per concern. Keep this shape; a reader of any SDK made by this skill
then knows where to look.

```
<package>/
  __init__.py        the public surface: root SDK, credentials, models, exceptions
  config.py          addresses and transport settings of one site instance
  hosts.py           one router ancestor per host
  exceptions.py      the exception hierarchy
  security.py        the session scheme
  models/            public result models, one module per area
  http/
    operations.py    operations of the API
    routers.py       routers, the root SDK and how it is opened
  auth/
    steps.py         sign-in steps as operations
    service.py       the service that runs the steps
tests/replay/
  factory.py         how the replay opens the SDK
  scenarios.toml     what the replay runs
examples/
  live_check.py      a run against the live site, for the user
```

A small API needs fewer files: drop `auth/` when there is no sign-in, fold `models/` into one
module. Do not add files the list does not have.

## Hosts

A site usually lives on several hosts. Each host is an empty router ancestor; a router inherits
the ancestor of its host and the root binds each ancestor to an address.

```python
from eazy_sdk import AsyncApi


class AccountHost(AsyncApi):
    """Host ``account``: the sign-in page."""


class MailHost(AsyncApi):
    """The mail host: pages and the JSON API."""
```

An operation that has to name another host (an `Origin` header, a return address in a form)
asks for it instead of spelling it:

```python
from eazy_sdk import Inject
from eazy_sdk.dependencies import service_url
from eazy_sdk.request import markers

ORIGIN = Inject(placement=markers.Header("Origin"), source=service_url(AccountHost))
REFERER = Inject(placement=markers.Header("Referer"), source=service_url(AccountHost, "/"))
RETURN_TO = Inject(placement=markers.Query("page"), source=service_url(MailHost, "/inbox"))
```

`service_url(Host)` is the bound address of the host; `service_url(Host, "/path")` is an
address on it.

Then a different `config` changes both where the SDK goes and everything that names the
address.

## Config

A frozen dataclass: one base URL per host, the transport settings, nothing else. Take every
value from the recording; write no address anywhere else in the package. The one exception
is the host pattern of `markers.Url(hosts=...)`, which has to be a literal.

```python
from dataclasses import dataclass

from curl_cffi import BrowserTypeLiteral


@dataclass(frozen=True, slots=True)
class SiteConfig:
    account_base_url: str = "https://account.site.example"
    mail_base_url: str = "https://mail.site.example"
    impersonate: BrowserTypeLiteral = "chrome"   # a plain ``str`` does not pass mypy
    timeout: float = 30.0
    proxy: str | None = None


DEFAULT_CONFIG = SiteConfig()
```

## Root

The root composes routers and is opened by one factory. Operations are never declared on the
root; a plain helper method is allowed.

```python
from typing import Self

from eazy_sdk import AsyncRoot, ClientConfig, Identity, Resilience, api_group, bind
from eazy_sdk.auth import Cookies, ensure_session
from eazy_sdk.auth.session import MemorySessionStore
from eazy_sdk.handlers.curl_cffi import AsyncCurlCffiZaprosHandler
from zapros import AsyncBaseHandler


class SiteSdk(AsyncRoot):
    cookies = Cookies()  # the site keeps its session in cookies: the library carries them

    mail = api_group(MailApi)
    # Sign-in steps, one router per host. The login service calls them, the user does not.
    login_pages = api_group(LoginPagesApi)
    mail_pages = api_group(MailPagesApi)

    @classmethod
    def open(
        cls,
        *,
        credentials: SiteLogin,
        config: SiteConfig = DEFAULT_CONFIG,
        handler: AsyncBaseHandler | None = None,
    ) -> Self:
        auth = SESSION.configure(
            credentials=credentials,
            service=SiteLoginService(),
            store=MemorySessionStore[SiteSession](),
        )
        return cls.from_handler(
            handler=handler
            or AsyncCurlCffiZaprosHandler(impersonate=config.impersonate, proxy=config.proxy),
            base_url=config.mail_base_url,
            bindings=(bind(AccountHost, base_url=config.account_base_url),),
            config=ClientConfig(
                resilience=Resilience(max_redirects=6, client_redirects=True, timeout=config.timeout)
            ),
            identity=Identity(auth=(auth,)),
        )

    async def signed_in(self) -> SiteSession:
        """Sign in if not signed in yet, without sending an API request."""
        return await ensure_session(self, scheme=SESSION)
```

- `handler=` is how the replay substitutes the transport: keep it in the signature.
- `base_url` is the address of the routers that are not bound; every other host gets a
  `bind(Host, base_url=...)`. `bind` needs a router of that host among the root's groups: a
  host that is only named through `service_url` still gets one, even an empty one.
- `Cookies()` on the root: declare it whenever the inventory lists cookies. The SDK never
  enumerates or forwards cookies by hand.
- `max_redirects` and `client_redirects=True`: set them when the inventory shows a redirect
  chain. `client_redirects` follows `<meta http-equiv="refresh">` stubs. An operation that
  declares a case on a redirect status reads that redirect itself; it is not followed.
- `SESSION`, the credentials type and the login service are in `auth-session.md`. An SDK
  without sign-in drops `credentials` and `auth` and passes `identity=Identity()`.

## Public surface

`__init__.py` exports the root, the credentials type, public models and exceptions, with an
explicit `__all__`. Sign-in step operations and wire models stay unexported.
