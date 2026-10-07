"""Final HTTP and browser roots assembled from the tutorial operations."""

from __future__ import annotations

from eazy_sdk import AsyncApi, AsyncRoot, api_group, op

from examples.mail.browser.session import MailboxApi
from examples.mail.http.encryption import SendEncryptedMessage
from examples.mail.http.messages import ListByOffset
from examples.mail.http.send import MAIL_BEARER
from examples.mail.http.session import GetCurrentUser


# region docs: mail-sdk-routers
# examples/mail/sdk.py
class AccountApi(AsyncApi):
    security = MAIL_BEARER

    current = op(GetCurrentUser)


class MessagesApi(AsyncApi):
    page = op(ListByOffset)
    send = op(SendEncryptedMessage)


class MailSdk(AsyncRoot):
    account = api_group(AccountApi)
    messages = api_group(MessagesApi)


class BrowserMailSdk(MailSdk):
    mailbox = api_group(MailboxApi)
# endregion docs: mail-sdk-routers


__all__ = ["AccountApi", "BrowserMailSdk", "MailSdk", "MessagesApi"]
