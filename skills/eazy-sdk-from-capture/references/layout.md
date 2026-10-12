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
```

Then a different `config` changes both where the SDK goes and everything that names the
address.

## Root

The root composes routers and owns nothing else. Operations are never declared on the root.

```python
from typing import Self

from eazy_sdk import AsyncRoot, ClientConfig, Identity, Resilience, api_group, bind
from eazy_sdk.auth import Cookies
from eazy_sdk.handlers.curl_cffi import AsyncCurlCffiZaprosHandler


class SiteSdk(AsyncRoot):
    cookies = Cookies()  # the site keeps its session in cookies: the library carries them

    mail = api_group(MailApi)
    login_pages = api_group(LoginPagesApi)

    @classmethod
    def open(cls, *, config: SiteConfig = DEFAULT_CONFIG, handler=None) -> Self:
        return cls.from_handler(
            handler=handler or AsyncCurlCffiZaprosHandler(impersonate=config.impersonate),
            base_url=config.mail_base_url,
            bindings=(bind(AccountHost, base_url=config.account_base_url),),
            config=ClientConfig(
                resilience=Resilience(max_redirects=6, client_redirects=True, timeout=30.0)
            ),
            identity=Identity(),
        )
```

- `Cookies()` on the root: declare it whenever the inventory lists cookies. The SDK never
  enumerates or forwards cookies by hand.
- `max_redirects` and `client_redirects=True`: set them when the inventory shows a redirect
  chain. `client_redirects` follows `<meta http-equiv="refresh">` stubs. An operation that
  declares a case on a redirect status reads that redirect itself; it is not followed.
- The factory takes `handler=` so the replay can substitute the transport.

## Config

A frozen dataclass with one base URL per host, `impersonate`, the timeout, and an optional
`proxy`. Take every value from the recording; write no address anywhere else.

## Public surface

`__init__.py` exports the root, the credentials type, public models and exceptions, with an
explicit `__all__`. Sign-in step operations and wire models stay unexported.
