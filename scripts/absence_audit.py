"""Negative release gate for the removed Eazy SDK execution architecture."""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMER_IDENTITY = "resp" + "lens"

REMOVED_MODULES = (
    "eazy_sdk/contract.py",
    "eazy_sdk/adapters",
    "eazy_sdk/clients/factories.py",
    "eazy_sdk/inject.py",
    "eazy_sdk/retry.py",
    "eazy_sdk/dependencies.py",
    "eazy_sdk/request/draft.py",
    "eazy_sdk/request/inputs.py",
    "eazy_sdk/request/middleware.py",
    "eazy_sdk/request/signing.py",
    "eazy_sdk/request/target.py",
    "eazy_sdk/response/descriptors.py",
    "eazy_sdk/response/result.py",
    "eazy_sdk/validation",
    "eazy_sdk/auth/manager.py",
    "eazy_sdk/auth/middleware.py",
    "eazy_sdk/auth/retry.py",
    "eazy_sdk/auth/state.py",
    "eazy_sdk/storage/auth_bridge.py",
    "eazy_sdk/storage/authstore.py",
    "eazy_sdk/protection.py",
)

REMOVED_SYMBOLS = {
    "AuthManager",
    "AuthMiddleware",
    "AuthState",
    "BytesError",
    "EmptyError",
    "EmptyInput",
    "EndpointCall",
    "EndpointContract",
    "HeaderOrder",
    "InboundMessageProtector",
    "JsonError",
    "QueryParameter",
    "QueryStringParameter",
    "PathParameter",
    "HeaderParameter",
    "CookieParameter",
    "DumpPolicy",
    "RequestDraft",
    "RequestTarget",
    "FrameProtector",
    "OutboundMessageProtector",
    "ResponsePolicyBuilder",
    "ResponseRetry",
    "ResponseTrigger",
    "TextError",
    "TransportRetry",
    "ValidatedResult",
    "ValidatedAsyncClient",
    "ValidatedSyncClient",
    "AdapterCapabilities",
    "AsyncPreparedAdapter",
    "SyncPreparedAdapter",
    "wrap_curl_cffi",
    "wrap_httpx",
    "wrap_requests",
    "wrap_wreq",
    "collect_injection",
    "signs_request",
    "apply_inbound_message_protectors",
    "apply_outbound_message_protectors",
    "compile_protectors",
    "BodyAccess",
    "CapableChallengeSolver",
    "NetworkIdentity",
    "NetworkIdentityContext",
    "NetworkIdentityExpectation",
    "NetworkIdentityProvider",
    "NetworkIdentityRequiredError",
    "NetworkIdentitySource",
    "ProtectionCapabilities",
    "ProtectionCapabilityMismatch",
    "ProtectionIdentityMismatch",
    "StaticNetworkIdentity",
    "resolve_network_identity",
}

PHASE17_PUBLIC_TEXT = (
    "EndpointContract",
    "EndpointCall",
    "client.execute(",
    "execute_with_response(",
)

PHASE21_REMOVED_BODY_PATHS = (
    "eazy_sdk/api.py",
    "eazy_sdk/_internal/http_operation.py",
    "eazy_sdk/clients/executor.py",
    "plugins/openapi/eazy_sdk_openapi/generator.py",
)

PHASE21_ROOT_CODEC_WORKAROUND = {
    "examples/flat_model_wire_body.py": (
        "RegisterUserBodyCodec",
        "EncodeContext",
        "Annotated[RegisterUser",
    ),
}

HTTP_DECORATOR_NAMES = {"delete", "get", "patch", "post", "put"}
DIRECT_HTTP_DECORATOR_IMPORT = re.compile(
    r"from[ \t]+eazy_sdk(?:\.codegen)?[ \t]+import[ \t]+"
    r"(\([^)]*\)|[A-Za-z_]\w*(?:[ \t]*,[ \t]*[A-Za-z_]\w*)*)",
    re.DOTALL,
)

REMOVED_IMPORTS = {
    "eazy_sdk.inject",
    "eazy_sdk.retry",
    "eazy_sdk.request.draft",
    "eazy_sdk.request.middleware",
    "eazy_sdk.request.signing",
    "eazy_sdk.request.target",
    "eazy_sdk.validation",
    "eazy_sdk.auth.manager",
    "eazy_sdk.auth.middleware",
    "eazy_sdk.auth.retry",
    "eazy_sdk.auth.state",
    "eazy_sdk.adapters",
    "eazy_sdk.clients.factories",
}

HIDDEN_PUBLIC_SYMBOLS = {
    "eazy_sdk.auth": {
        "AuthExecution",
        "AuthPlacement",
        "AuthProviderIdentity",
        "AuthProviders",
        "SessionKey",
        "SessionProvider",
    },
    "eazy_sdk.clients": {"ExecutionCore", "ExecutionResult", "ExecutionRuntime"},
    "eazy_sdk.response": {
        "MalformedOutcome",
        "ParseAttempt",
        "ResponseParser",
        "SuccessOutcome",
    },
    "eazy_sdk.request": {"PreparedRequest", "RequestPreparer", "SignaturePlan"},
    "eazy_sdk.dependencies": {"DependencyCaches", "RequestRequirement", "ResultBinding"},
    "eazy_sdk.protection": {
        "ChallengeSolverBindings",
        "PrivateBindings",
        "ProtectionBundle",
        "ResponseSignal",
        "SolverRequirement",
    },
}

LEGACY_FACTORY_OPTION = re.compile(
    r"wrap_(?:httpx|requests|curl_cffi|wreq)\([^()\n]*(?:auth|dependencies|middleware|"
    r"protections|rate_limiter)="
)

ACCOUNT_CORE_FORBIDDEN_IMPORTS = {
    "eazy_sdk.adapters",
    "eazy_sdk.clients",
    "eazy_sdk.request",
    "eazy_sdk.response",
    "curl_cffi",
    "httpx",
    "playwright",
    "requests",
    "selenium",
    "wreq",
}

WEBSOCKET_FORBIDDEN_TRANSPORT_IMPORTS = {
    "aiohttp",
    "websockets",
    "wsproto",
}

CRYPTO_IMPLEMENTATION_IMPORTS = {"Crypto", "cryptography", "nacl", "pycryptodome"}


def audit() -> list[str]:
    failures: list[str] = []
    tracked_and_untracked = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    former_bytes = FORMER_IDENTITY.encode()
    for raw_relative in tracked_and_untracked:
        if not raw_relative:
            continue
        identity_relative = Path(raw_relative.decode())
        path = ROOT / identity_relative
        if not path.exists():
            continue
        if FORMER_IDENTITY in identity_relative.as_posix().lower():
            failures.append(f"former identity remains in path: {identity_relative}")
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except PermissionError:
            continue
        if b"\0" not in content and former_bytes in content.lower():
            failures.append(f"former identity remains in content: {identity_relative}")

    for relative in REMOVED_MODULES:
        candidate = ROOT / relative
        present = candidate.is_file() or (
            candidate.is_dir()
            and any("__pycache__" not in path.parts for path in candidate.rglob("*"))
        )
        if present:
            failures.append(f"removed path still exists: {relative}")

    for relative in PHASE21_REMOVED_BODY_PATHS:
        if "wire_body" in (ROOT / relative).read_text(encoding="utf-8"):
            failures.append(f"removed wire_body operation path remains: {relative}")

    for relative, fragments in PHASE21_ROOT_CODEC_WORKAROUND.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment in source:
                failures.append(
                    f"removed root-codec projection workaround {fragment!r}: {relative}"
                )

    for path in (ROOT / "plugins" / "openapi" / "tests" / "snapshots").rglob("*"):
        if path.is_file() and "wire_body" in path.read_text(encoding="utf-8"):
            failures.append(
                f"removed wire_body decorator remains in snapshot: {path.relative_to(ROOT)}"
            )

    execution_core_definitions: list[Path] = []
    compiler_entry_definitions: list[Path] = []
    for directory in (ROOT / "eazy_sdk", ROOT / "plugins"):
        for path in directory.rglob("*.py"):
            if _is_generated_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    if node.name in REMOVED_SYMBOLS:
                        failures.append(f"removed symbol {node.name}: {path.relative_to(ROOT)}")
                    if isinstance(node, ast.ClassDef) and node.name == "ExecutionCore":
                        execution_core_definitions.append(path.relative_to(ROOT))
                    if (
                        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                        and node.name == "compile_endpoint"
                    ):
                        compiler_entry_definitions.append(path.relative_to(ROOT))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(
                            alias.name == item or alias.name.startswith(item + ".")
                            for item in REMOVED_IMPORTS
                        ):
                            failures.append(
                                f"removed import {alias.name}: {path.relative_to(ROOT)}"
                            )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and any(
                        node.module == item or node.module.startswith(item + ".")
                        for item in REMOVED_IMPORTS
                    )
                ):
                    failures.append(f"removed import {node.module}: {path.relative_to(ROOT)}")
                if (
                    "plugins" in path.parts
                    and isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and (
                        node.module == "eazy_sdk._internal"
                        or node.module.startswith("eazy_sdk._internal.")
                    )
                ):
                    failures.append(
                        f"plugin imports private core {node.module}: {path.relative_to(ROOT)}"
                    )
                if "websocket" in path.parts and isinstance(node, ast.Import | ast.ImportFrom):
                    imported_names = (
                        [node.module]
                        if isinstance(node, ast.ImportFrom)
                        else [alias.name for alias in node.names]
                    )
                    for imported_name in imported_names:
                        if imported_name is not None and any(
                            imported_name == item or imported_name.startswith(item + ".")
                            for item in WEBSOCKET_FORBIDDEN_TRANSPORT_IMPORTS
                        ):
                            failures.append(
                                "WebSocket core imports a transport implementation outside Zapros: "
                                f"{imported_name}: {path.relative_to(ROOT)}"
                            )
                if "crypto" in path.parts and isinstance(node, ast.Import | ast.ImportFrom):
                    crypto_imports = (
                        [node.module]
                        if isinstance(node, ast.ImportFrom)
                        else [alias.name for alias in node.names]
                    )
                    for imported_name in crypto_imports:
                        if imported_name is not None and any(
                            imported_name == item or imported_name.startswith(item + ".")
                            for item in CRYPTO_IMPLEMENTATION_IMPORTS
                        ):
                            failures.append(
                                "core crypto contains a cipher-library import "
                                f"{imported_name}: {path.relative_to(ROOT)}"
                            )
                imported: str | None = None
                if isinstance(node, ast.ImportFrom):
                    imported = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_account_core(path) and any(
                            alias.name == item or alias.name.startswith(item + ".")
                            for item in ACCOUNT_CORE_FORBIDDEN_IMPORTS
                        ):
                            failures.append(
                                "account lifecycle imports transport dependency "
                                f"{alias.name}: {path.relative_to(ROOT)}"
                            )
                if (
                    _is_account_core(path)
                    and imported is not None
                    and any(
                        imported == item or imported.startswith(item + ".")
                        for item in ACCOUNT_CORE_FORBIDDEN_IMPORTS
                    )
                ):
                    failures.append(
                        "account lifecycle imports transport dependency "
                        f"{imported}: {path.relative_to(ROOT)}"
                    )
                if (
                    "eazy_sdk" in path.parts
                    and "models" not in path.parts
                    and isinstance(node, ast.Attribute)
                    and node.attr in {"model_dump", "model_validate", "model_fields", "model_copy"}
                ):
                    failures.append(
                        f"direct model-library duck typing {node.attr}: {path.relative_to(ROOT)}"
                    )

    expected_executor = [Path("eazy_sdk/clients/executor.py")]
    if execution_core_definitions != expected_executor:
        failures.append(
            "expected one HTTP ExecutionCore path, found "
            f"{[path.as_posix() for path in execution_core_definitions]!r}"
        )
    expected_compiler = [Path("eazy_sdk/_internal/http_compiler.py")]
    if compiler_entry_definitions != expected_compiler:
        failures.append(
            "expected one compile_endpoint path, found "
            f"{[path.as_posix() for path in compiler_entry_definitions]!r}"
        )

    public_text_roots = (
        ROOT / "examples",
        ROOT / "docs-site" / "src",
        ROOT / "plugins" / "asyncapi" / "README.md",
        ROOT / "plugins" / "openapi" / "README.md",
        ROOT / "plugins" / "presets" / "README.md",
        ROOT / "plugins" / "sqlmodel" / "README.md",
        ROOT / "plugins" / "xml" / "README.md",
        ROOT / "README.md",
    )
    for entry in public_text_roots:
        paths = (entry,) if entry.is_file() else entry.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".mdx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for symbol in REMOVED_SYMBOLS:
                if symbol in text:
                    failures.append(f"removed symbol text {symbol}: {path.relative_to(ROOT)}")
            for text_fragment in PHASE17_PUBLIC_TEXT:
                if text_fragment in text:
                    failures.append(
                        f"removed phase-17 API text {text_fragment}: {path.relative_to(ROOT)}"
                    )
            for match in DIRECT_HTTP_DECORATOR_IMPORT.finditer(text):
                direct_import_names = set(
                    re.findall(r"\b[A-Za-z_]\w*\b", match.group(1))
                )
                direct = sorted(direct_import_names & HTTP_DECORATOR_NAMES)
                if direct:
                    failures.append(
                        "direct HTTP decorator import remains "
                        f"{direct}: {path.relative_to(ROOT)}"
                    )
            if LEGACY_FACTORY_OPTION.search(text):
                failures.append(f"legacy client factory option: {path.relative_to(ROOT)}")
            if "wire_body=" in text:
                failures.append(
                    f"removed wire_body decorator remains: {path.relative_to(ROOT)}"
                )

    for module_name, names in HIDDEN_PUBLIC_SYMBOLS.items():
        module = importlib.import_module(module_name)
        for name in names:
            if hasattr(module, name):
                failures.append(f"low-level public export remains: {module_name}.{name}")

    root = importlib.import_module("eazy_sdk")
    for name in (
        "EndpointContract",
        "EndpointCall",
        "DumpPolicy",
        "ValidatedAsyncClient",
        "ValidatedSyncClient",
        "wrap_curl_cffi",
        "wrap_httpx",
        "wrap_requests",
        "wrap_wreq",
        *sorted(HTTP_DECORATOR_NAMES),
    ):
        if hasattr(root, name):
            failures.append(f"removed public export remains: eazy_sdk.{name}")

    codegen = importlib.import_module("eazy_sdk.codegen")
    for name in HTTP_DECORATOR_NAMES:
        if hasattr(codegen, name):
            failures.append(f"removed public export remains: eazy_sdk.codegen.{name}")

    api_module = importlib.import_module("eazy_sdk.api")
    for name in HTTP_DECORATOR_NAMES:
        if hasattr(api_module, name):
            failures.append(f"direct HTTP decorator export remains: eazy_sdk.api.{name}")

    executor = ROOT / "eazy_sdk" / "clients" / "executor.py"
    for path in (ROOT / "eazy_sdk").rglob("*.py"):
        if path == executor or "adapters" in path.parts:
            continue
        if ".emit(" in path.read_text(encoding="utf-8"):
            failures.append(f"direct emit outside executor: {path.relative_to(ROOT)}")
    return failures


def _is_account_core(path: Path) -> bool:
    return "accounts" in path.parts and path.name != "http.py"


def _is_generated_path(path: Path) -> bool:
    return any(
        part == "__pycache__"
        or part in {".venv", "node_modules", "site-packages"}
        or part.startswith(".phase")
        for part in path.parts
    )


def main() -> int:
    failures = audit()
    if failures:
        print("ABSENCE AUDIT FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "OK: former identity, removed modules, transport-neutral account boundary, "
        "symbols, imports and execution loops are clean"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
