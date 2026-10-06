"""Адаптер: ответ, перехваченный браузером, разбирает объявление `eazy-sdk`.

Ответ из браузера — обычный HTTP-ответ, поэтому и разбирать его должно то же
объявление, что и в HTTP-SDK:

    COMPANY_CASES = Responses(
        success=(Success(201, Json(CreateReply)),),
        errors=(
            Error(409, Json(ApiProblem), exception=_company_exists),
            Error(StatusRange(500, 599), Json(ApiProblem), exception=_portal_broken),
        ),
    )

Отсюда бесплатно берутся выбор случая по статусу и типу содержимого, разбор модели,
условия по телу (`condition=`), объявленные исключения и внятные ошибки, когда ответ
не подошёл ни под один случай (`UnexpectedResponseError`) или подошёл, но не
разобрался (`MalformedResponseError`).

Исключения остаются своими: `eazy-sdk` принимает не только класс `ApiError`, но и
фабрику `(модель, контекст) -> Exception`, поэтому ошибки портала наследуются от
`PageError`, а не от чужой иерархии.

Ядро знает только протокол `Decoder`; этот адаптер — одна из его реализаций.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eazy_sdk.response import NormalizedResponse, ResponseContext

# `SuccessOutcome` в публичный `eazy_sdk.response` не вынесен, хотя без него успешный
# исход не отличить от отказа, не полагаясь на `unwrap() -> None`. Место, которое
# стоит попросить экспортировать наверх.
from eazy_sdk.response.cases import SuccessOutcome

if TYPE_CHECKING:
    from eazy_sdk.response import Responses
    from eazy_sdk_browser.network import ResponseView


@dataclass(frozen=True, slots=True)
class SdkCases[T]:
    """Объявление ответов `eazy-sdk` в роли декодера браузерного ответа."""

    responses: Responses[T]
    method: str | None = None

    def decode(self, response: ResponseView) -> T:
        """Выбрать случай по статусу и телу: вернуть модель или бросить объявленное."""
        context: ResponseContext[object] = ResponseContext(
            response=NormalizedResponse(
                status_code=response.status,
                url=response.url,
                method=self.method,
                headers=response.header_map(),
                body=response.body,
            )
        )
        outcome = self.responses.inspect(context)
        if isinstance(outcome, SuccessOutcome):
            return outcome.value
        # Остальные исходы существуют ради исключения: отказ, неожиданный ответ,
        # неразобранное тело, неоднозначное объявление.
        outcome.unwrap()
        msg = f"{type(outcome).__name__} не бросил исключение"
        raise RuntimeError(msg)


def sdk_cases[T](responses: Responses[T], *, method: str | None = None) -> SdkCases[T]:
    """Объявить разбор перехваченного ответа набором случаев `eazy-sdk`."""
    return SdkCases(responses, method)
