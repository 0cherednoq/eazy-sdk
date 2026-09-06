"""Read and create typed JSONPlaceholder posts over the public demo API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk import Client, ClientConfig, Http, HttpOperation, Path, Query, Resilience, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import markers

BASE_URL = "https://jsonplaceholder.typicode.com"


class BlogPost(BaseModel):
    id: int
    user_id: int = Field(validation_alias="userId")
    title: str
    body: str


class GetPost(BaseModel, HttpOperation[BlogPost]):
    """A Pydantic operation: ``frozen`` is required, the alias owns the wire name."""

    model_config = ConfigDict(frozen=True, serialize_by_alias=True)
    __http__ = Http.get("/posts/{post_id}", operation_id="getPost")

    post_id: Path[int]


class ListPosts(BaseModel, HttpOperation[list[BlogPost]]):
    model_config = ConfigDict(frozen=True, serialize_by_alias=True)
    __http__ = Http.get("/posts", operation_id="listPosts")

    user_id: Query[int] = Field(serialization_alias="userId")


class CreatePost(BaseModel, HttpOperation[BlogPost]):
    model_config = ConfigDict(frozen=True, serialize_by_alias=True)
    __http__ = Http.post("/posts", operation_id="createPost", success={201: BlogPost})

    user_id: Annotated[int, markers.JsonField()] = Field(serialization_alias="userId")
    title: Annotated[str, markers.JsonField()]
    body: Annotated[str, markers.JsonField()]


class JsonPlaceholderApi(SyncApi):
    get_post = op(GetPost)
    list_posts = op(ListPosts)
    create_post = op(CreatePost)


def main() -> None:
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(),
        config=ClientConfig(resilience=Resilience(timeout=20)),
    ) as client:
        posts = JsonPlaceholderApi(client)

        first = posts.get_post(post_id=1)

        by_user = posts.list_posts(user_id=1)

        created = posts.create_post(
            user_id=1,
            title="Eazy SDK SDK example",
            body="JSONPlaceholder simulates this write without persisting it.",
        )

    print(f"GET /posts/1 -> {first.user_id}: {first.title}")
    print(f"GET /posts?userId=1 -> {len(by_user)} posts")
    print(f"POST /posts -> {created.id}: {created.title}")


if __name__ == "__main__":
    main()
