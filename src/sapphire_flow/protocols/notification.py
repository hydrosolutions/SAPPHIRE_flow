from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.enums import NotificationChannel


@runtime_checkable
class NotificationAdapter(Protocol):
    def send(
        self,
        channel: NotificationChannel,
        recipients: list[str],
        subject: str,
        body: str,
        alert: Alert | None = None,
    ) -> None:
        raise NotImplementedError
