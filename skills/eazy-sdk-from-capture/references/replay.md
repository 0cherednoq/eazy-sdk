# Replay and the gates

The replay runs the real SDK with the transport replaced: every request the SDK makes is
answered with the recorded response, matched by method, host and path in the recorded order.
Nothing reaches the network.

## `tests/replay/factory.py`

One function that opens the SDK on a given handler. Keyword arguments come from the
scenario's `[scenario.options]`.

```python
from site_sdk import SiteLogin, SiteSdk


def open_sdk(handler, *, username: str, password: str):
    return SiteSdk.open(credentials=SiteLogin(username, password), handler=handler)
```

Return the SDK itself; an async context manager is entered for you.

## `tests/replay/scenarios.toml`

```toml
[sdk]
factory = "tests.replay.factory:open_sdk"
mode = "async"                       # "sync" for a SyncRoot SDK

[[scenario]]
name = "wrong password"
exchanges = "1-2"                    # "1-2,5" or [1, 2, 5]
call = "mail.list"                   # a dotted path from the SDK: "mail.list.items", "signed_in"
[scenario.options]
username = "__SECRET_email_1__"
password = "__SECRET_password_1__"
[scenario.expect]
raises = "InvalidCredentials"

[[scenario]]
name = "sign in and list the inbox"
exchanges = "3-9"
call = "mail.list"
[scenario.options]
username = "__SECRET_email_1__"
password = "__SECRET_password_2__"
[scenario.args]
folder = 0
limit = 20
[scenario.expect]
returns = "ThreadsPage"
fields = { threads_total = 71 }
lengths = { threads = 20 }

[[exclude]]
exchanges = "10-12"
reason = "background polling of the unread counter"
```

- Placeholders from the inventory (`__SECRET_password_1__`) are replaced with the real values
  at run time. Never write a real value here: gate G8 looks for them.
- `call` is resolved by attribute access from the SDK and called with `[scenario.args]`.
  A protected operation signs in first, so a wrong-password scenario may call any protected
  operation and expect the refusal. To replay the sign-in alone, call a root helper such as
  `signed_in` (`layout.md`).
- One scenario per mark of the recording, or per call the SDK offers. A scenario lists every
  exchange its call makes, including the sign-in chain when the call triggers it.
- `raises` / `returns` name a class anywhere in the MRO. `fields` compares a value at a
  dotted path, `lengths` a length (`"."` for the result itself).
- `[[exclude]]` needs a reason a reader will accept. Noise the inventory left out is excluded
  by itself.

Take the expected numbers from the inventory (array lengths and totals are in the JSON
shape); when a value is not there, run the replay once and read the outcome.

## The gates

| Gate | Passes when | Usual cause of a failure |
|---|---|---|
| G1 | the package and all its modules import | a typo, a wrong import |
| G2 | `mypy --strict` is clean | a missing annotation, a model that does not match `HttpOperation[T]` |
| G3 | `ruff check` is clean | |
| G4 | every scenario ends as expected | a response case does not match the recorded response |
| G5 | the SDK makes no request the capture lacks | a wrong path or host; a step the site does not have |
| G6 | every exchange is used or excluded | the SDK skips a step; a scenario lists too much |
| G7 | every request matches the recorded one | a missing or extra parameter, wrong form order, a missing header or cookie |
| G8 | no secret of the capture is in the project | a real value pasted into code, a test or the report |
| G9 | nothing is imported from private modules | `from eazy_sdk.x._y import ...` |
| G10 | the skill's references match the library | the installed library is older or newer than the skill |

`skipped` is not `pass`. G2 and G3 are skipped when the tool is not installed in the SDK's
environment: install it (`uv add --dev mypy ruff`) rather than report a skip.

## Reading a replay

```
[FAIL] sign in and list the inbox: mail.list -> MailboxNotOpened: ... <- MalformedResponseError: ...
       #5 GET mail.site.example/inbox/ -> 200
         differs: query 'limit': the SDK sends '50', recorded '20'
       NOT IN THE CAPTURE: GET mail.site.example/api/v2/threads ?offset
       never requested: exchanges [9]
```

- **The outcome line** shows the exception chain. `MalformedResponseError` names the model
  and the field that was not found: fix the selector or the model, not the scenario.
  `UnexpectedResponseError` means no case claimed the response: a variant is undeclared.
  `AmbiguousResponseError` means two cases claimed it: tighten a criterion.
- **differs** is G7. Fix the operation until the request equals the recorded one. A value
  that looks like a timestamp is allowed to differ. A header listed as a *note* is one the
  browser sent and the SDK did not; it is not a failure, decide whether the site needs it.
- **NOT IN THE CAPTURE** is G5: the SDK asked for something that was never recorded.
- **never requested** is G6: the scenario claims an exchange the call does not make. Either
  the SDK is missing a step, or the exchange belongs to another scenario or to `[[exclude]]`.
- **capture warning** about lossy text: a case that matches on text the export destroyed
  cannot pass. Match on what survived, or get a better recording.

A pass means the SDK is consistent with the recording. It does not mean the live site accepts
the SDK: header order, TLS and anything the recording could not hold are checked by a live
run, which is the user's to make.
