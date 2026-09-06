# eazy-sdk-adaptix

`eazy-sdk-adaptix` lets an SDK serialize its models with an [Adaptix](https://adaptix.readthedocs.io)
retort instead of describing the same rules twice. Register the retort as a model adapter and the
operations it serves are dumped and loaded by it.

```python
from adaptix import Retort, name_mapping
from eazy_sdk import Client, SyncApi
from eazy_sdk.models import default_model_adapters
from eazy_sdk.serialization import Serialization
from eazy_sdk_adaptix import adaptix_models

RETORT = Retort(recipe=[name_mapping(RegisterUser, map={"full_name": "fullName"})])

models = adaptix_models(
    default_model_adapters(),
    retort=RETORT,
    types=(RegisterUser,),
    names={RegisterUser: {"full_name": "fullName"}},
)
sdk = UsersApi(client, serialization=Serialization(models=models))
```

The retort serves only the types it is given. Adaptix reads every dataclass, so an adapter that
claimed all of them would collide with the built-in dataclass adapter on models the retort was
never told about. `adaptix_models` therefore installs this adapter *in place of* the built-in one:
it answers for every dataclass, routes the named types through the retort, and hands the rest to
the built-in implementation unchanged — so a registry never holds two adapters claiming one model.

`names` is the Python-to-wire table for those types: a retort does not hand its `name_mapping`
back, so the table is declared beside it once — the same list you already write when configuring
the retort.
