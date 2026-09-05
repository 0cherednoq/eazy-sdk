"""Audit built Eazy SDK distributions for the rewrite release contract."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tarfile
import tomllib
import zipfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

PACKAGES = {
    "eazy_sdk": ("eazy_sdk/py.typed", "pyproject.toml"),
    "eazy_sdk_accounts": (
        "eazy_sdk_accounts/py.typed",
        "plugins/accounts/pyproject.toml",
    ),
    "eazy_sdk_asyncapi": (
        "eazy_sdk_asyncapi/py.typed",
        "plugins/asyncapi/pyproject.toml",
    ),
    "eazy_sdk_html": ("eazy_sdk_html/py.typed", "plugins/html/pyproject.toml"),
    "eazy_sdk_openapi": ("eazy_sdk_openapi/py.typed", "plugins/openapi/pyproject.toml"),
    "eazy_sdk_presets": ("eazy_sdk_presets/py.typed", "plugins/presets/pyproject.toml"),
    "eazy_sdk_sqlmodel": (
        "eazy_sdk_sqlmodel/py.typed",
        "plugins/sqlmodel/pyproject.toml",
    ),
    "eazy_sdk_xml": ("eazy_sdk_xml/py.typed", "plugins/xml/pyproject.toml"),
}

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Top-level import names provided by each mandatory distribution a wheel may declare.
# Every unconditional import of a packaged module must resolve to the standard library,
# to the wheel's own package or to one of its mandatory (non-extra) dependencies.
RE_EXTRA_MARKER = re.compile(r"extra\s*==\s*['\"]([^'\"]+)['\"]")
RE_DISTRIBUTION_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
DISTRIBUTION_IMPORTS = {
    "zapros": ("zapros",),
    "httpx": ("httpx",),
    "requests": ("requests",),
    "curl-cffi": ("curl_cffi",),
    "pydantic": ("pydantic",),
    "datamodel-code-generator": ("datamodel_code_generator",),
    "parsel": ("parsel",),
    "sqlmodel": ("sqlmodel",),
    "aiosqlite": ("aiosqlite",),
    "sqlalchemy": ("sqlalchemy",),
    "pyyaml": ("yaml",),
    "eazy-sdk": ("eazy_sdk",),
    "eazy-sdk-accounts": ("eazy_sdk_accounts",),
    "eazy-sdk-html": ("eazy_sdk_html",),
    "eazy-sdk-presets": ("eazy_sdk_presets",),
    "eazy-sdk-sqlmodel": ("eazy_sdk_sqlmodel",),
    "eazy-sdk-openapi": ("eazy_sdk_openapi",),
    "eazy-sdk-asyncapi": ("eazy_sdk_asyncapi",),
    "eazy-sdk-xml": ("eazy_sdk_xml",),
}

FORBIDDEN_CORE = (
    "eazy_sdk/adapters/",
    "eazy_sdk/clients/factories.py",
    "eazy_sdk/contract.py",
    "eazy_sdk/dependencies.py",
    "eazy_sdk/inject.py",
    "eazy_sdk/retry.py",
    "eazy_sdk/security.py",
    "eazy_sdk/types.py",
    "eazy_sdk/request/body.py",
    "eazy_sdk/request/draft.py",
    "eazy_sdk/request/inputs.py",
    "eazy_sdk/request/server.py",
    "eazy_sdk/request/signing.py",
    "eazy_sdk/request/target.py",
    "eazy_sdk/response/descriptors.py",
    "eazy_sdk/response/media.py",
    "eazy_sdk/response/result.py",
    "eazy_sdk/response/streaming.py",
    "eazy_sdk/protection.py",
    "eazy_sdk/validation/",
)

REQUIRED_PHASE29_CORE = (
    "eazy_sdk/protection/__init__.py",
    "eazy_sdk/protection/advanced.py",
)

FORBIDDEN_PHASE29_SOURCE = (
    "BodyAccess",
    "CapableChallengeSolver",
    "NetworkIdentity",
    "ProtectionCapabilities",
    "ProtectionCapabilityMismatch",
    "resolve_network_identity",
)

FORBIDDEN_PHASE17_SOURCE = (
    "EndpointContract",
    "EndpointCall",
    "EmptyInput",
)

FORBIDDEN_PHASE18_SOURCE = (
    "DumpPolicy",
    "SyncPreparedAdapter",
    "AsyncPreparedAdapter",
    "AdapterCapabilities",
    "wrap_httpx",
    "wrap_requests",
    "wrap_curl_cffi",
    "wrap_wreq",
    "ValidatedSyncClient",
    "ValidatedAsyncClient",
)

FORBIDDEN_PHASE20_SOURCE = (
    "OutboundMessageProtector",
    "InboundMessageProtector",
    "FrameProtector",
    "apply_outbound_message_protectors",
    "apply_inbound_message_protectors",
    "compile_protectors",
)

FORBIDDEN_PHASE21_PATHS = {
    "eazy_sdk/api.py",
    "eazy_sdk/compile/http_operation.py",
    "eazy_sdk/clients/executor.py",
    "eazy_sdk_openapi/generator.py",
}

FORBIDDEN_CRYPTO_DEPENDENCIES = ("cryptography", "pycryptodome", "pynacl")

FORBIDDEN_MODEL_DUCK_TYPING = (
    "model_dump",
    "model_validate",
    "model_fields",
    "model_copy",
    "dataclasses.is_dataclass",
)


def _wheel_for(directory: Path, package: str) -> Path:
    matches = sorted(directory.glob(f"{package}-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one wheel for {package}, found {len(matches)}")
    return matches[0]


def _sdist_for(directory: Path, package: str) -> Path:
    matches = sorted(directory.glob(f"{package}-*.tar.gz"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one sdist for {package}, found {len(matches)}")
    return matches[0]


def _unconditional_imports(source: str) -> set[str]:
    """Top-level names imported at module import time (outside try/TYPE_CHECKING)."""

    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                names.update(alias.name.split(".")[0] for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module is not None:
                    names.add(child.module.split(".")[0])
            elif isinstance(child, ast.Try):
                for handler in child.handlers:
                    visit(handler)
                for statement in child.orelse + child.finalbody:
                    visit(statement)
            elif isinstance(child, ast.If):
                test = ast.unparse(child.test)
                if "TYPE_CHECKING" in test:
                    for statement in child.orelse:
                        visit(statement)
                else:
                    visit(child)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            elif isinstance(child, ast.ClassDef | ast.With | ast.For | ast.While | ast.Match):
                visit(child)

    visit(ast.parse(source))
    return names


# Modules that are imported only behind an install hint (``Client.httpx`` and friends);
# their unconditional imports must be covered by the named extra instead.
OPTIONAL_MODULES = {
    "eazy_sdk/handlers/httpx.py": "httpx",
    "eazy_sdk/handlers/requests.py": "requests",
    "eazy_sdk/handlers/curl_cffi.py": "curl-cffi",
}


def _distributions(metadata: Message, extra: str | None) -> set[str]:
    """Distribution names required unconditionally, or by ``extra`` when given."""

    found: set[str] = set()
    for value in metadata.get_all("Requires-Dist", []) or []:
        marker = RE_EXTRA_MARKER.search(value)
        if (marker.group(1) if marker is not None else None) != extra:
            continue
        match = RE_DISTRIBUTION_NAME.match(value)
        if match is not None:
            found.add(match.group(1).casefold().replace("_", "-"))
    return found


def _import_failures(
    wheel_name: str,
    package: str,
    names: list[str],
    metadata: Message,
    archive: zipfile.ZipFile,
) -> list[str]:
    failures: list[str] = []

    def provided_by(distributions: set[str]) -> set[str]:
        provided: set[str] = set()
        for distribution in sorted(distributions):
            imports = DISTRIBUTION_IMPORTS.get(distribution)
            if imports is None:
                failures.append(
                    f"{wheel_name}: dependency {distribution!r} has no entry in "
                    "DISTRIBUTION_IMPORTS"
                )
                continue
            provided.update(imports)
        return provided

    mandatory = set(sys.stdlib_module_names) | {package}
    mandatory |= provided_by(_distributions(metadata, None))
    for name in names:
        if not name.endswith(".py"):
            continue
        allowed = set(mandatory)
        extra = OPTIONAL_MODULES.get(name)
        if extra is not None:
            allowed |= provided_by(_distributions(metadata, extra))
        for imported in sorted(_unconditional_imports(archive.read(name).decode("utf-8"))):
            if imported not in allowed:
                failures.append(
                    f"{wheel_name}: {name} imports {imported!r} unconditionally, but no "
                    f"{'mandatory' if extra is None else repr(extra) + ' extra'} dependency "
                    "provides it"
                )
    return failures


def audit(directory: Path) -> None:
    failures: list[str] = []
    for package, (marker, pyproject_path) in PACKAGES.items():
        with (REPOSITORY_ROOT / pyproject_path).open("rb") as pyproject_file:
            project = tomllib.load(pyproject_file)["project"]
        expected_name = project["name"]
        expected_version = project["version"]
        expected_license = project["license"]

        wheel = _wheel_for(directory, package)
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if marker not in names:
                failures.append(f"{wheel.name}: missing {marker}")
            metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            if metadata["Name"] != expected_name:
                failures.append(
                    f"{wheel.name}: expected project name {expected_name!r}, "
                    f"found {metadata['Name']!r}"
                )
            if metadata["Version"] != expected_version:
                failures.append(
                    f"{wheel.name}: expected version {expected_version!r}, "
                    f"found {metadata['Version']!r}"
                )
            if metadata["License-Expression"] != expected_license:
                failures.append(
                    f"{wheel.name}: expected license {expected_license!r}, "
                    f"found {metadata['License-Expression']!r}"
                )
            if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
                failures.append(f"{wheel.name}: missing packaged LICENSE text")
            classifiers = metadata.get_all("Classifier", [])
            failures.extend(_import_failures(wheel.name, package, names, metadata, archive))
            if (
                "a" in expected_version
                and "Development Status :: 3 - Alpha" not in classifiers
            ):
                failures.append(f"{wheel.name}: alpha release is missing the Alpha classifier")
            if package == "eazy_sdk":
                failures.extend(
                    f"{wheel.name}: contains legacy path {path}"
                    for path in FORBIDDEN_CORE
                    if any(name == path or name.startswith(path) for name in names)
                )
                failures.extend(
                    f"{wheel.name}: missing phase-29 path {path}"
                    for path in REQUIRED_PHASE29_CORE
                    if path not in names
                )
                mandatory = [
                    value
                    for value in metadata.get_all("Requires-Dist", [])
                    if "extra ==" not in value and "extra == " not in value
                ]
                if mandatory != ["zapros==0.16.0"]:
                    failures.append(
                        f"{wheel.name}: expected only zapros==0.16.0 as a core dependency, "
                        f"found {mandatory!r}"
                    )
                lowered_requirements = "\n".join(metadata.get_all("Requires-Dist", [])).casefold()
                for dependency in FORBIDDEN_CRYPTO_DEPENDENCIES:
                    if dependency in lowered_requirements:
                        failures.append(
                            f"{wheel.name}: core declares crypto dependency {dependency!r}"
                        )
            for name in names:
                if not name.endswith(".py"):
                    continue
                source = archive.read(name).decode("utf-8")
                if name in FORBIDDEN_PHASE21_PATHS and "wire_body" in source:
                    failures.append(
                        f"{wheel.name}: removed wire_body operation path in {name}"
                    )
                for fragment in FORBIDDEN_PHASE17_SOURCE:
                    if fragment in source:
                        failures.append(
                            f"{wheel.name}: removed phase-17 API {fragment!r} in {name}"
                        )
                if package == "eazy_sdk":
                    for fragment in FORBIDDEN_PHASE18_SOURCE:
                        if fragment in source:
                            failures.append(
                                f"{wheel.name}: removed phase-18 API {fragment!r} in {name}"
                            )
                    for fragment in FORBIDDEN_PHASE20_SOURCE:
                        if fragment in source:
                            failures.append(
                                f"{wheel.name}: removed phase-20 API {fragment!r} in {name}"
                            )
                    for fragment in FORBIDDEN_PHASE29_SOURCE:
                        if fragment in source:
                            failures.append(
                                f"{wheel.name}: removed phase-29 API {fragment!r} in {name}"
                            )
                    if not name.startswith("eazy_sdk/models/"):
                        for fragment in FORBIDDEN_MODEL_DUCK_TYPING:
                            if fragment in source:
                                failures.append(
                                    f"{wheel.name}: direct model-library duck typing "
                                    f"{fragment!r} in {name}"
                                )

        sdist = _sdist_for(directory, package)
        with tarfile.open(sdist, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if not any(name.endswith(f"/{marker}") for name in names):
                failures.append(f"{sdist.name}: missing {marker}")
            if not any(name.endswith("/LICENSE") for name in names):
                failures.append(f"{sdist.name}: missing packaged LICENSE text")
            if package == "eazy_sdk":
                failures.extend(
                    f"{sdist.name}: contains legacy path {path}"
                    for path in FORBIDDEN_CORE
                    if any(
                        name.endswith(f"/{path}")
                        or f"/{path}" in name
                        for name in names
                    )
                )
                failures.extend(
                    f"{sdist.name}: missing phase-29 path {path}"
                    for path in REQUIRED_PHASE29_CORE
                    if not any(name.endswith(f"/{path}") for name in names)
                )
                for member in members:
                    if not member.isfile() or not member.name.endswith(".py"):
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    source = extracted.read().decode("utf-8")
                    for fragment in FORBIDDEN_PHASE29_SOURCE:
                        if fragment in source:
                            failures.append(
                                f"{sdist.name}: removed phase-29 API {fragment!r} "
                                f"in {member.name}"
                            )

    if failures:
        raise RuntimeError("\n".join(failures))
    print(
        "OK: wheels/sdists have matching release metadata, licenses, typing markers, "
        "the Zapros boundary, the phase-29 protection package, no legacy paths, and every "
        "unconditional import is covered by a mandatory dependency"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    audit(args.directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
