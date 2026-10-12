# What not to write

Read this before you call the SDK finished. Each line is a mistake that was made for real in
a hand-written SDK on this library, and the mechanism that replaces it.

| Do not | Do |
|---|---|
| a raw HTTP client call inside the SDK (`curl_cffi`, `httpx`, `requests`) | an operation; for a caller-given address `markers.Url(hosts=...)` |
| a function that classifies a `Location` string | models with `Location(...)` fields in `success=` / `errors=` |
| a function that unwraps `{status, body}` in every operation | `unwrap` and `errors` on the routers' common ancestor |
| translating outcomes to exceptions with `if` after the call | exception classes in `errors=` and `fallback=` |
| parsing a page with `re.search` / `json.loads` in a function | a document model with `Regex` / `CSS` / `XPath` fields |
| a `Cookie` header built from a string, cookies copied between steps | `cookies = Cookies()` on the root |
| a loop that follows redirects or `meta refresh` | `Resilience(max_redirects=..., client_redirects=True)` |
| a generator method on a router for pages | `__pages__ = Pages....` |
| a facade class that re-exports router methods | routers published with `api_group()`; the root is the facade |
| a wire model plus a mapper for a response that already has the public shape | the public model in `HttpOperation[T]` |
| every query parameter as a public field | technical constants in `inject` |
| a body built by a plain function when one value has two wire names | `BodyProjection` with a form model |
| the address of another host as a literal | `service_url(Host)` |
| a second way into the SDK next to the root factory | one factory; variants are its arguments |
| a proxy set on the raw session only | pass the proxy to the handler as well |
| `from eazy_sdk.<pkg>._private import ...` | public modules only; a missing public name is a library gap |
| an exception text that contains a URL with its query | the path only, or no address |
| an error class for something the recording does not show | nothing; list it as not observed |
| retries, sleeps or rate limits inside operations | `Resilience(retry=...)` on the client, or leave it to the caller |

## When the declaration is not enough

Do not reach for a function to cover a gap in the library. Write `LIBRARY-GAPS.md` in the
project:

- what the site does (from the inventory, with exchange numbers);
- the declaration you would have written;
- why the library refuses or cannot express it;
- a minimal example.

Mark the operation blocked in the plan and the report. The decision belongs to the owner of
the library.

## Before you finish

- Does every public field belong to the caller?
- Is every response variant you kept from the inventory a declared case, and every declared
  case backed by an exchange?
- Does any module import a transport library other than through the handler factory?
- Would the SDK still work against another instance of the site if only `config` changed?
