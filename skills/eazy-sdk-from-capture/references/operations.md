# Declaring requests

An operation is a frozen class. Its fields are what the caller passes; everything else the
site demands is declared in `__http__` and never reaches the public signature.

```python
from dataclasses import dataclass, field
from typing import Annotated, Any

from eazy_sdk import UNSET, AsyncApi, Http, HttpOperation, Inject, Omittable, Query, op
from eazy_sdk.request import markers


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(HttpOperation[ThreadsPage]):
    """Messages of a folder."""

    __http__ = Http.get(
        "/api/v1/threads/status/smart",
        inject=(
            Inject(placement=markers.Query("last_modified"), source="1"),
            Inject(placement=markers.Query("_"), source=now_ms),
        ),
    )

    folder: Query[int] = 0
    offset: Query[int] = 0
    limit: Query[int] = 50
    search: Query[Omittable[str]] = UNSET


class MailApi(AsyncApi):
    list = op(ListThreads)
```

- Always the class form with `op()`. Do not use the `@api.get` decorator.
- `HttpOperation[T]` names the result. `T` is the public model; no intermediate wire model
  unless the wire shape and the public shape really differ.
- A field that is sometimes not sent is `Omittable[T] = UNSET`.
- Fields are keyword-only; give real Python defaults.

## What is public and what goes to `inject`

Read the parameter's line in the inventory.

| The inventory says | Declare |
|---|---|
| `constant`, technical (a format flag, a version, a client name) | `Inject(..., source="value")` |
| `timestamp` | `Inject(..., source=callable)`, recomputed on every attempt |
| `constant`, but the caller may reasonably want another value (page size, sort) | a public field with that default |
| `varies` between samples | a public field |
| comes from `the caller` or `typed by the user` | a public field, or credentials of the sign-in |
| comes from a cookie, a `Location` or a response body of an earlier exchange | not a field: session state or a step result (see `auth-session.md`) |
| `secret:*` that comes from the session | a `Placed` field of the session model |
| `unknown`, changes between samples | stop: it is computed by a page script. Name it in the plan |

The `source` of an `Inject` is a constant, a zero-argument callable (sync or async), or a
`service_url(...)`. Query and form values are strings on the wire: a constant the inventory
shows as `false` or `1` is `source="false"`, `source="1"`. A constant that merely looks like a
query string (`opener=account&twoSteps=1`) stays one string constant.

With one sample every value looks constant. Say in the plan which constants you are treating
as technical on the strength of a single sample; the user decides the doubtful ones.

A header the browser sets by itself (`User-Agent`, `Accept-Encoding`, `sec-*`) belongs to the
transport: `impersonate` sends it. Declare only what the site's own code adds: `Origin`,
`X-Requested-With`, headers with the site's prefix, and the `Referer` of a form the site
checks. The `Referer` of a GET is where the person happened to be; do not declare it.

## Placements

`Path`, `Query`, `Header`, `Cookie`, `JsonField`, `Form` are short forms of
`Annotated[T, markers.X()]`. Use the full form when the marker takes arguments:

```python
trace: Annotated[str, markers.Header("X-Trace")]
```

- **Path parameter**: `"/threads/{thread_id}"` with a field `thread_id: Path[str]`.
- **JSON body**: one `JsonField[T]` per top-level key. Use `markers.JsonBody()` on a model
  field only for a nested document.
- **Form body**: one `Form[T]` per field, **in the recorded order** (`body field order` in
  the inventory). A field with a default sends the default.
- **The caller gives the whole address** (a download link from an earlier response):
  `Http.get(markers.Url(hosts=("site.example", "*.site.example")))` and one field
  `url: Url[str]` from `eazy_sdk.request`.

## A value that is itself JSON or a query string

The inventory marks such a value `json` or `query-string`. Give it a model, so the caller gets
names and types instead of a bare dict, and encode it with a codec:

```python
import json
from dataclasses import dataclass
from typing import Annotated, Literal

from eazy_sdk.codecs import ScalarEncodeContext
from eazy_sdk.request import markers
from pydantic import BaseModel


class Sort(BaseModel):
    type: Literal["date", "size"] = "date"
    order: Literal["asc", "desc"] = "desc"


@dataclass(frozen=True, slots=True)
class JsonCodec:
    """A model or a plain value sent as compact JSON in one parameter."""

    name: str = "json-compact"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json", exclude_none=True)
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


JsonValue = markers.Query(codec=JsonCodec())

# in the operation:
#   sort: Annotated[Sort, JsonValue] = Sort()
```

- Model fields come from the keys the inventory shows in the recorded values. A key seen with
  one value only is a `Literal` of that value or a plain `str`: say in the plan which you chose.
- Use a pydantic model or a dataclass. A `TypedDict` is refused by the library when the
  operation is called; note it in `LIBRARY-GAPS.md` if the user asks for one.
- The library does not validate the value against the field's type at call time: the type is
  for the editor and for mypy.
- The same codec serves a form field: `markers.Form(codec=JsonCodec())`.
- Keep `dict[str, Any]` only for a value whose keys are data (an id-to-id map), never for a
  value with a fixed set of keys.

Repeated query keys (`tag=a&tag=b`) are not supported for declared parameters: one name, one
value.

## One value under several names

When the inventory says "same value under several names" (`Login` and `username`), the caller
passes it once. The operation's fields are the caller's; a private form model spells the wire:

```python
from eazy_sdk import Http, HttpOperation
from eazy_sdk.dependencies import Injected
from eazy_sdk.request import Body, BodyProjection
from pydantic import BaseModel


class _CredentialsForm(BaseModel):
    Login: str
    username: str
    Password: str
    password: str
    lang: str = "ru_RU"

    @classmethod
    def of(cls, step: "SubmitCredentials", injected: Injected) -> "_CredentialsForm":
        return cls(
            Login=step.username, username=step.username,
            Password=step.password, password=step.password,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        projection=BodyProjection(
            target=_CredentialsForm, using=_CredentialsForm.of, encoding=Body.form()
        ),
    )

    username: str
    password: str
```

Fields of a projected operation carry no placement: the projection owns the body. The order of
the form model's fields is the wire order. Constants of the form are defaults of the form
model.

When a form field is an address on another host (a return URL), the projection asks for it as
a dependency; `injected` is where the declared `requires` arrive:

```python
from eazy_sdk.dependencies import dependency, service_url

RETURN_TO = dependency(str, name="return-address", provide=service_url(MailHost, "/inbox"))

# in the form model:
#     page: str
#     ... cls(..., page=str(injected[RETURN_TO.dependency]))
# in the operation:
#     __http__ = Http.post("/cgi-bin/auth", requires=(RETURN_TO,), projection=...)
```

## A query that is part of the address

A constant query the site treats as part of the endpoint may stay in the path:
`Http.post("/cgi-bin/auth?from=web&project=login")`.
