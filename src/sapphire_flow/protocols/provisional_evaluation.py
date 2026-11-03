from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.provisional_evaluation import (
        EvaluationScope,
        EvaluationSubject,
    )


class EvaluationGrantReader(Protocol):
    def find_active_grant(
        self, scope: EvaluationScope, subject: EvaluationSubject
    ) -> UUID | None: ...
