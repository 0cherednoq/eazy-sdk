# Declaring responses

A response case is `status × media type × how the body is read × a criterion`. The inventory
lists the variants of every operation under **Responses**; each variant you keep becomes one
case. Two variants on one status are two cases told apart by a criterion.

## The plain case

`HttpOperation[T]` alone is the success case: status 2xx, body read into `T`. The family is
read from the model: a model with selector fields is a document (HTML), any other is JSON.

```python
from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation


class Account(BaseModel):
    email: str
    name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GetAccount(HttpOperation[Account]):
    __http__ = Http.get("/api/v1/user/short")
```

Model fields come from the inventory's JSON shape. A field marked `optional` there gets a
default. A field the API sends as either a value or `null` also needs one. Declare the fields
the SDK returns, not every field of the response; ignore the rest (`extra="ignore"` on
pydantic).

## Several outcomes

```python
__http__ = Http.post(
    "/cgi-bin/auth",
    success={302: [SignedIn, CodeRequired]},
    errors={302: [CaptchaRequired, AccountBanned]},
    fallback=InvalidCredentials,
)
```

- `success=` and `errors=` map a status (`200`, `"4xx"`, `"5xx"`) to a model, a list of
  models, an exception class, or `(Model, factory)`.
- Which dictionary a model is in says whether the outcome is a success or an error. A model
  never says that about itself.
- `fallback=` takes what no case claimed. Use it for the general refusal.
- The library does not pick "the first that fits": a response two cases accept ends in
  `AmbiguousResponseError`. Make the criteria exclusive.
- `HttpOperation[A | B]` when success has several models.

## Outcome told by a redirect

The inventory shows it as variants with different `Location` on one status.

```python
from dataclasses import dataclass
from typing import Annotated

from eazy_sdk.response import Const, Location


@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]


@dataclass(frozen=True, slots=True)
class AccountLocked:
    url: Annotated[str, Location(path="/login*")]
    errno: Annotated[str, Location.query("errno"), Const("25")]


@dataclass(frozen=True, slots=True)
class Rejected:
    url: Annotated[str | None, Location()] = None
```

`Location(host=, path=, query=, contains=)` on a field is both the criterion and the source:
the case claims a response pointing there and the field receives the absolute address.
`Location.query(name)` reads one query parameter. An operation that declares a case on a
redirect status reads the redirect; the client does not follow it.

As a bare condition: `success={302: Bytes(when=Location() & ~Location(query={"fail": ...}))}`
with `Bytes` from `eazy_sdk.response`.

## Outcome told by a field of the body

The inventory shows `body marks` on a variant, and an **Envelope candidate** when every JSON
response of a host shares top-level keys.

```python
from typing import Annotated, Any

from eazy_sdk.response import Const, Not, OneOf, Payload


class Found(BaseModel):
    ok: Annotated[bool, Const(True)]
    result: Payload[Account]          # the operation returns this field


class TokenRejected(BaseModel):
    status: Annotated[int, OneOf(401, 403)]
    body: Any = None


class Refused(BaseModel):
    status: Annotated[int, Not(200, 401, 403)]
    body: Any = None
```

`Const`, `OneOf`, `Not` are statements about the body. Values are compared with their types
(`1` is not `True`). `Payload[T]` marks the field a success returns.

### An envelope declared once

When every operation of a host answers `{status, body}`, declare the envelope on the common
ancestor of its routers and keep operations unaware of it:

```python
from eazy_sdk.response import Error, Json


class JsonApi(MailHost):
    security = SESSION
    unwrap = "/body"                     # operations get the content of `body`
    errors = (
        Error(200, Json(TokenRejected), exception=token_rejected, reauth=True),
        {200: (Refused, refused), "4xx": (Refused, refused), "5xx": (Refused, refused)},
    )
```

- `unwrap` is a JSON pointer applied to success bodies.
- An `errors` entry is `(Model, factory)`; the factory is
  `(error, context: ResponseContext[object]) -> Exception`.
- `reauth=True`: the response means a stale session; the library refreshes it once and
  repeats the request. Use it only when the recording shows such a refusal.
- A case with a criterion outranks a success without one, so a refusal body is never parsed
  into a model full of defaults.

When the recording shows the envelope but no refusal, declare `unwrap` alone and no `errors`:
a refusal that was never observed has no shape to declare.

A case that has to read the whole envelope under a router `unwrap` (an "unchanged" answer
told by `status`) says so itself: `Json(Unchanged, unwrap="")` from `eazy_sdk.response`.

Declare an envelope only when the inventory shows it. With `one operation` confidence, say so
in the plan.

## HTML pages

A value inside a page is a field of a document model. Never parse a page with functions.

```python
from typing import Annotated

from eazy_sdk.response import FromCookie
from eazy_sdk_html import CSS, Regex, XPath
from pydantic import BaseModel


class LoginPage(BaseModel):
    csrf: Annotated[str, Regex(r'"csrf":"([^"]+)"') | FromCookie("act")]


class MailPage(BaseModel):
    title: Annotated[str, CSS("title::text")]
    token: Annotated[str, Regex(r'"token":"([^"]+)"')]
    build: Annotated[str, XPath("//meta[@name='build']/@content")] = ""
```

- Take the pattern from the inventory: a value origin `body of #N, regex ...` gives the
  regex and the context around the value. When the inventory reports several occurrences,
  anchor the pattern to the text that precedes the right one.
- `A | B` tries sources left to right.
- `FromCookie("act")` reads a cookie **this response sets**, not the cookie jar. The inventory
  line "comes from cookie `act` set by #1" means: the model of the operation behind exchange
  #1 gets a field `Annotated[str, FromCookie("act")]`, and the next step passes it on.
- A required field is a criterion: a page without it is not this page. That is how a redirect
  stub and the real page served from one address are told apart. Do not make a field required
  on text the recording lost (a `LOSSY TEXT` variant): match on a value that survived.
- `Regex` reads the document text as received; `CSS` and `XPath` need the `html` extra.

## Other bodies

`Bytes()`, `Text()`, `Empty()` from `eazy_sdk.response`, used as a case:
`success={200: Bytes()}` with `HttpOperation[bytes]`.

## Exceptions

One base exception for the SDK; one class per observed refusal.

### Naming an outcome

A protocol often tells an outcome by a code that means nothing by itself (`errno=25`). A
recording made with the skill's recorder also knows what the person saw: under a response
variant the inventory says `then the page showed: «…»`, and a scenario lists it among its
steps. That text is the site's own name for the outcome.

- Name the class after what the page said (`AccountBlocked`), not after the code
  (`SignInRefused25`). Keep the code as the criterion: the text is for people and may be
  translated or reworded, the code is what the response carries.
- Put the site's words into the docstring, quoted, with the exchange number.
- Without such a line (a HAR, a Burp export, a page that showed nothing new) the meaning is
  unknown: name the class after the fact (`SignInRefused` with the code in the docstring),
  list it in the plan as unnamed and ask the user what it means.
- A line the inventory only counts (`N lines appeared`) is a rendered list, not a message.

```python
from typing import ClassVar

from eazy_sdk.response import ApiError


class SiteError(Exception):
    """Base of every error of this SDK."""


class Refusal[T](SiteError, ApiError[T]):
    """A refusal the site explained; the model is in ``error``."""

    reason: ClassVar[str]

    def __str__(self) -> str:
        return self.reason


class InvalidCredentials(Refusal[Rejected]):
    reason = "Wrong login or password"
```

An `ApiError[Model]` subclass in `errors=` or `fallback=` names its own model. Keep addresses
with a query out of exception text: the query carries tokens and the account address.
