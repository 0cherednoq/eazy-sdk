"""Read and create typed JSONPlaceholder posts over the public demo API."""

from __future__ import annotations

from typing import Annotated, TypedDict, Unpack

from pydantic import BaseModel, Field

from eazy_sdk import Client, ClientConfig, SyncApi, api
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import JsonField, Path, Query
from eazy_sdk.response import Json, Responses, Success

BASE_URL = "https://jsonplaceholder.typicode.com"


class BlogPost(BaseModel):
    id: int
    user_id: int = Field(validation_alias="userId")
    title: str
    body: str


class GetPostRequest(TypedDict):
    post_id: Annotated[int, Path()]


class ListPostsRequest(TypedDict):
    user_id: Annotated[int, Query("userId")]


class CreatePostRequest(TypedDict):
    user_id: Annotated[int, JsonField("userId")]
    title: Annotated[str, JsonField()]
    body: Annotated[str, JsonField()]


POST_RESPONSE = Responses(
    success=(Success(200, Json(BlogPost)),)
)
POSTS_RESPONSE = Responses(
    success=(Success(200, Json(list[BlogPost])),)
)
CREATE_RESPONSE = Responses(
    success=(Success(201, Json(BlogPost)),)
)


class JsonPlaceholderApi(SyncApi):
    @api.get("/posts/{post_id}", operation_id="getPost", responses=POST_RESPONSE)
    def get_post(self, **request: Unpack[GetPostRequest]) -> BlogPost:
        raise NotImplementedError

    @api.get("/posts", operation_id="listPosts", responses=POSTS_RESPONSE)
    def list_posts(self, **request: Unpack[ListPostsRequest]) -> list[BlogPost]:
        raise NotImplementedError

    @api.post("/posts", operation_id="createPost", responses=CREATE_RESPONSE)
    def create_post(self, **request: Unpack[CreatePostRequest]) -> BlogPost:
        raise NotImplementedError


def main() -> None:
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(),
        config=ClientConfig(timeout=20),
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
