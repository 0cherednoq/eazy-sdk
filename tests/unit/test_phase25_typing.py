from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


POSITIVE = r'''# pyright: strict, reportUnknownVariableType=false
from dataclasses import dataclass
from typing import Annotated, TypedDict, Unpack, assert_type

from eazy_sdk import AsyncApi, PrepareOptions, PreparedCall, api, api_group
from eazy_sdk.ext import ParseAttempt, ParsedValue, ResponseParser
from eazy_sdk.request import Path
from eazy_sdk.response import Json, ResponseContext, callable_parser


@dataclass
class User:
    name: str


class ExplicitApi(AsyncApi):
    @api.get("/users", response=Json())
    async def list_users(self, *, page: int = 1) -> User:
        raise NotImplementedError


class UserRequest(TypedDict):
    user_id: Annotated[int, Path()]


class UnpackedApi(AsyncApi):
    @api.get("/users/{user_id}", response=Json())
    async def get_user(self, **request: Unpack[UserRequest]) -> User:
        raise NotImplementedError


class Root(AsyncApi):
    users = api_group(ExplicitApi)


def parse_user(context: ResponseContext[object]) -> ParseAttempt[User]:
    return ParsedValue(User(context.text.value or ""))


parser = callable_parser(User, parse_user)
assert_type(parser, ResponseParser)


async def proof(explicit: ExplicitApi, unpacked: UnpackedApi, root: Root) -> None:
    assert_type(await explicit.list_users(page=2), User)
    assert_type(
        await explicit.list_users.prepare(page=2, options=PrepareOptions()),
        PreparedCall,
    )
    assert_type(await unpacked.get_user(user_id=7), User)
    assert_type(root.users, ExplicitApi)
'''


NEGATIVE = r'''# pyright: strict
from dataclasses import dataclass

from eazy_sdk import AsyncApi, api
from eazy_sdk.ext import ParsedValue
from eazy_sdk.response import Json, ResponseContext, callable_parser


@dataclass
class User:
    name: str


class UsersApi(AsyncApi):
    @api.get("/users", response=Json())
    async def users(self, *, page: int = 1) -> User:
        raise NotImplementedError


def wrong_parser(context: ResponseContext[object]) -> ParsedValue[str]:
    return ParsedValue("wrong")


bad_parser = callable_parser(User, wrong_parser)


async def invalid(api_instance: UsersApi) -> None:
    await api_instance.users(page="wrong")
    await api_instance.users(unknown=1)


@api.get("/typo", responze=Json())
async def typo(self: AsyncApi) -> User:
    raise NotImplementedError
'''


def _run(checker: str, source: Path) -> subprocess.CompletedProcess[str]:
    command = (
        [sys.executable, "-m", "mypy", "--strict", str(source)]
        if checker == "mypy"
        else [
            sys.executable,
            "-m",
            "basedpyright",
            "--pythonversion",
            "3.13",
            str(source),
        ]
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase25_public_authoring_types_are_preserved(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase25-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "positive.py"
        source.write_text(POSITIVE, encoding="utf-8")
        result = _run(checker, source)
    # basedpyright exits 1 on style warnings (reportUnusedCallResult) on some platforms;
    # the contract of this test is "no type errors".
    assert result.returncode == 0 or "0 errors" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase25_typing_rejects_bad_calls_parsers_and_decorator_typos(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase25-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "negative.py"
        source.write_text(NEGATIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "callable_parser" in output
    assert "unknown" in output
    assert "responze" in output or "/typo" in output
