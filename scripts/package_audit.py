"""Audit built Eazy SDK distributions for the rewrite release contract."""

from __future__ import annotations

import argparse
import tarfile
import tomllib
import zipfile
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
        "the Zapros boundary, the phase-29 protection package, and no legacy paths"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    audit(args.directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
