# eazy-sdk-xml

`eazy-sdk-xml` adds a standard-library XML body codec and response extractor to Eazy SDK. Use it
when an HTTP API accepts or returns simple XML and you want to keep XML support outside the core
runtime.

```python
from typing import Annotated, TypedDict, Unpack

from eazy_sdk import SyncApi, api
from eazy_sdk.response import Extracted, Responses, Success
from eazy_sdk_xml import ElementTreeXmlCodec, XmlBody, XmlResponse

codec = ElementTreeXmlCodec(root_name="user")
responses = Responses(success=(Success(200, Extracted(dict, using=XmlResponse(codec))),))


class CreateUserRequest(TypedDict):
    body: Annotated[dict[str, object], XmlBody(codec)]


class UsersApi(SyncApi):
    @api.post("/users", operation_id="createUser", responses=responses)
    def create_user(self, **request: Unpack[CreateUserRequest]) -> dict[str, object]:
        raise NotImplementedError
```

`ElementTreeXmlCodec` supports nested mappings and repeated list or tuple elements. For XML
namespaces, attributes, mixed content, or schema validation, implement the `XmlCodec` protocol with
the XML library used by your application.
