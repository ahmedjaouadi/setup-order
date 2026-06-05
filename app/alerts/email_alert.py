from __future__ import annotations


class EmailAlert:
    def send(self, subject: str, message: str) -> None:
        raise NotImplementedError("Email alerts are planned for V2")

