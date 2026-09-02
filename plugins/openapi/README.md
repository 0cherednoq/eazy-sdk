# eazy-sdk-openapi

Generate thin Eazy SDK SDK packages from OpenAPI 3.0.x, 3.1.x, or 3.2.x:

```bash
eazy-sdk-openapi openapi.yaml generated --package-name petstore_sdk
```

The generator emits Pydantic models and separate declarative sync/async API classes. Each
operation stores its annotated path, query, header, cookie, and body fields in one generated
`TypedDict`; the method exposes them through `**request: Unpack[OperationRequest]`. Its regular
call returns the parsed success value; `.with_response(...)` returns a
`ResponseEnvelope` with the same value plus status, headers, and the raw response.

```python
api = AsyncAPI.httpx(base_url="https://museum.example.test")

confirmation = await api.tickets.buy_museum_tickets(body=request)
envelope = await api.tickets.buy_museum_tickets.with_response(body=request)
```

All generated calls delegate to the shared Eazy SDK executor. The canonical `x-eazy-sdk`
namespace describes dependencies, signing, replay, sessions, and protection policies. Unknown
fields and obsolete extension forms are rejected with an operation ID and JSON Pointer.

An operation-level `x-eazy-sdk.projection` can name separate public `source` and request-body
`target` schemas plus an application requirement. Resolve the requirement to a static mapper
import at generation time:

```bash
eazy-sdk-openapi openapi.yaml generated --package-name registration_sdk \
  --projection register-user=application.projections:register_to_wire
```

The generated method exposes the source fields as expanded kwargs and keeps the target wire model
private. Specs without this extension retain the normal wire-shaped `body=` parameter.

Every package also contains `openapi-compatibility.json`, which reports executable OpenAPI
behavior that is only partially represented. Current notable limits are deterministic selection
of one request media type, raw bytes for non-JSON/non-text responses, bearer injection for OAuth2
unless session acquisition is declared, no typed response-header extraction, no inbound
callbacks/webhooks, local references only, and the first server alternative only.

The regression corpus vendors fixed Redocly Museum and Swagger Petstore revisions. Run it with:

```bash
uv run pytest -q plugins/openapi/tests/test_real_world_schemas.py
```

After reviewing an intentional generator change, regenerate snapshots with:

```bash
uv run python scripts/update_openapi_snapshots.py
```
