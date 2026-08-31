"""SQLModel/SQLAlchemy account-storage v2 backend for Eazy SDK."""

from __future__ import annotations

from eazy_sdk_sqlmodel.adapter import (
    SqlConcurrencyError,
    SqlResourceConflictError,
    SqlStorageError,
)
from eazy_sdk_sqlmodel.codecs import PlainPydanticCodec, SqlValueCodec, SqlValueCodecError
from eazy_sdk_sqlmodel.factory import (
    DEFAULT_MODELS,
    SqlAccounts,
    SqlAccountStorage,
    SqlAccountWorkspace,
    SqlModelConfigurationError,
    SqlStorageModels,
    build_sqlmodel_storage,
    create_all,
    open_workspace,
)
from eazy_sdk_sqlmodel.migration import (
    MigrationAlreadyAppliedError,
    MigrationConflictError,
    MigrationError,
    MigrationIssue,
    MigrationReport,
    inspect_v1,
    migrate_v1_to_v2,
)
from eazy_sdk_sqlmodel.models import (
    DEFAULT_PROVIDER,
    BaseAccount,
    BaseAccountEvent,
    BaseAccountLink,
    BaseSession,
    BaseVerification,
)
from eazy_sdk_sqlmodel.registration import SqlRegistrationStore, SqlRegistrationTransactionError
from eazy_sdk_sqlmodel.schema import schema_ddl
from eazy_sdk_sqlmodel.session_store import SqlSessionStore

__all__ = [
    "DEFAULT_MODELS",
    "DEFAULT_PROVIDER",
    "BaseAccount",
    "BaseAccountEvent",
    "BaseAccountLink",
    "BaseSession",
    "BaseVerification",
    "MigrationAlreadyAppliedError",
    "MigrationConflictError",
    "MigrationError",
    "MigrationIssue",
    "MigrationReport",
    "PlainPydanticCodec",
    "SqlAccountStorage",
    "SqlAccountWorkspace",
    "SqlAccounts",
    "SqlConcurrencyError",
    "SqlModelConfigurationError",
    "SqlRegistrationStore",
    "SqlRegistrationTransactionError",
    "SqlResourceConflictError",
    "SqlSessionStore",
    "SqlStorageError",
    "SqlStorageModels",
    "SqlValueCodec",
    "SqlValueCodecError",
    "build_sqlmodel_storage",
    "create_all",
    "inspect_v1",
    "migrate_v1_to_v2",
    "open_workspace",
    "schema_ddl",
]
