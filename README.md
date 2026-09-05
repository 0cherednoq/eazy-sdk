# Eazy SDK

Eazy SDK is a transport-independent runtime for typed HTTP and async WebSocket SDKs. HTTP and
WebSocket use separate lifecycle runtimes while sharing model adapters, typed cases, auth values,
and protection primitives.

> **Status:** `0.2.0a4` is an alpha release with the post-review remediation: guard layer, transport ports, core/compile layering, accounts/html plugins and an exception hierarchy overhaul. Python 3.13 or
> newer is required. Release artifacts are hosted on GitHub; PyPI publication is deferred.
>
> Alphas rename without deprecated aliases. The docs page `more/migration` lists every
> `old name -> new name` for 0.2.0a3 -> 0.2.0a4 -> 0.2.0a5.

```bash
pip install \
  "eazy-sdk[httpx,pydantic] @ https://github.com/0cherednoq/eazy-sdk/releases/download/v0.2.0a4/eazy_sdk-0.2.0a4-py3-none-any.whl"
```

For WebSocket SDKs and AsyncAPI 3.0 generation:

```bash
pip install \
  "eazy-sdk[websocket] @ https://github.com/0cherednoq/eazy-sdk/releases/download/v0.2.0a4/eazy_sdk-0.2.0a4-py3-none-any.whl" \
  "eazy-sdk-asyncapi[yaml] @ https://github.com/0cherednoq/eazy-sdk/releases/download/v0.2.0a4/eazy_sdk_asyncapi-0.2.0a4-py3-none-any.whl"
eazy-sdk-asyncapi asyncapi.yaml generated --package-name market_stream
```

```python
from typing import Annotated

from pydantic import BaseModel

from eazy_sdk import Client, Json, Path, SyncApi, api


class User(BaseModel):
    id: int
    name: str


class UsersApi(SyncApi):
    @api.get("/users/{user_id}", operation_id="getUser", response=Json())
    def get_user(self, *, user_id: Annotated[int, Path()]) -> User:
        raise NotImplementedError


with Client.httpx(base_url="https://api.example") as client:
    users = UsersApi(client)
    user = users.get_user(user_id=42)
    envelope = users.get_user.with_response(user_id=42)
```

`response=Json()` infers the model from the return annotation; `Bytes()`, `Text()`, and `Html()`
work the same way, and `responses=Responses(...)` declares several success/error cases.
`Client(base_url=...)` uses Zapros' standard network handler; `Client.httpx()`,
`Client.requests()`, and `Client.curl_cffi()` build the first-party handlers, and `handler=`
accepts any Zapros handler.

`api` is the narrow HTTP-decorator namespace. It provides `get`, `post`, `put`, `patch`, `delete`,
`head`, `options`, `trace`, and `request`; import `SyncApi`, `AsyncApi`, and runtime types
separately.

For reusable or generated request schemas, expose the same inputs through
`**request: Unpack[TypedDict]`. Both authoring styles compile to the same request plan; do not mix
them in one operation.

When caller-facing kwargs and the protocol JSON have different shapes, declare one
`BodyProjection`. The public method stays flat while the target model owns nesting, aliases,
defaults, and private wire fields:

```python
from typing import TypedDict, Unpack

from eazy_sdk import AsyncApi, api
from eazy_sdk.request import BodyProjection, JsonBody


class RegisterUser(TypedDict):
    login: str
    email: str


REGISTER_BODY = BodyProjection(
    RegisterUser,
    RegisterUserWire,
    register_to_wire,
    JsonBody(),
)


class RegistrationApi(AsyncApi):
    @api.post("/register", body=REGISTER_BODY, responses=REGISTER_RESPONSES)
    async def register(self, **request: Unpack[RegisterUser]) -> RegisteredUser:
        raise NotImplementedError
```

Projection runs once per HTTP attempt before body encoding, crypto, and signing. Adaptix can
provide the typed mapper but remains optional; a plain callable follows the same contract.

Core uses Zapros as its HTTP and WebSocket boundary. Extras include `httpx`, `requests`,
`curl-cffi`, `websocket`, `pydantic`, `html`, `sqlmodel`, and `all`. OpenAPI generation is provided
by `eazy-sdk-openapi`, AsyncAPI 3.0 generation by `eazy-sdk-asyncapi`, and Cloudflare/reCAPTCHA
policies by `eazy-sdk-presets`.

Application-owned field and whole-payload encryption use the shared `eazy_sdk.crypto` profiles for
HTTP and WebSocket. Eazy SDK owns stage ordering and wire metadata, while applications provide the
algorithm and key lifecycle; see the [payload crypto guide](docs-site/src/content/docs/guides/payload-crypto.mdx).

See the [documentation](docs-site/src/content/docs/index.mdx), [examples](examples/README.md), and
the authoritative [implementation plan](docs/implementation/README.md).

Development gates:

```bash
uv run pytest -q
uv run mypy
uv run ruff check
uv run python scripts/docs_freshness.py check
```

Eazy SDK is released under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) for
the development workflow and [SECURITY.md](SECURITY.md) for private vulnerability reports.
