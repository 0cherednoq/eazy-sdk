# eazy-sdk-presets

Immutable Cloudflare Challenge Pages, Turnstile, reCAPTCHA v2/v3 and Enterprise descriptors for the
Eazy SDK signal/reaction/before-call runtime. Solvers are supplied directly or through identity-based
DI; presets perform no hidden network I/O and do not own retries.

```python
from eazy_sdk import ClientConfig
from eazy_sdk_presets import cloudflare, host

guard = cloudflare.challenge_pages(
    scope=host("api.example"),
    solver=my_solver,
)
config = ClientConfig().with_protection(guard)
```

The solver implements only the typed challenge-to-solution contract. Browser, JavaScript, remote
API, and WASM choices stay inside the solver. Managed clearance belongs to one client/handler
session. To rotate a proxy or browser profile, create a new handler and SDK client for the new
lease. The core package remains installable without presets or browser/parser dependencies.
