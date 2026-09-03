"""AccountWorkspace: thin aggregate that wires the storage services."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eazy_sdk_accounts.storage.entities import (
    Account,
    AccountEvent,
    AccountEventRecord,
    AccountLink,
    AccountLinkRecord,
    AccountRecord,
    Restriction,
    RestrictionRecord,
    Session,
    SessionRecord,
    Verification,
    VerificationRecord,
)
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.services.history import History
from eazy_sdk_accounts.storage.services.links import AccountLinks
from eazy_sdk_accounts.storage.services.pool import AccountPool
from eazy_sdk_accounts.storage.services.restrictions import Restrictions
from eazy_sdk_accounts.storage.services.sessions import Sessions
from eazy_sdk_accounts.storage.services.verifications import Verifications
from eazy_sdk_accounts.storage.storage import Storage


class AccountWorkspace[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
]:
    """Wires one instance of each storage service over a single Storage. Delegates only."""

    def __init__(
        self,
        storage: Storage[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID],
    ) -> None:
        self.storage = storage
        self.accounts = Accounts(storage)
        self.sessions = Sessions(storage)
        self.verifications = Verifications(storage)
        self.restrictions = Restrictions(storage)
        self.links = AccountLinks(storage)
        self.history = History(storage)
        self.pool = AccountPool(storage)
