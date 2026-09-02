# eazy-sdk-presets

Immutable Cloudflare Challenge Pages, Turnstile, reCAPTCHA v2/v3 and Enterprise descriptors for the
Eazy SDK signal/reaction/before-call runtime. Solvers are supplied directly or through identity-based
DI; presets perform no hidden network I/O and do not own retries.

```python
from eazy_sdk import ClientConfig
from eazy_sdk.protection import NetworkIdentity
from eazy_sdk_presets import cloudflare, host

guard = cloudflare.challenge_pages(
    scope=host("api.example"),
    solver=my_solver,
)
config = ClientConfig(
    network_identity=NetworkIdentity(proxy="edge-proxy-1"),
).with_protection(guard)
```

The solver declares its JavaScript/browser capabilities. Network-scoped clearance solvers also
return the attempt's `context.network_identity` as `expected_identity`; mismatches fail before
state publication or replay. The core package remains installable without presets or
browser/parser dependencies.
