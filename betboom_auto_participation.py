from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


_SUCCESS_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано)|"
    r"вы\s+(?:уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено)",
    re.IGNORECASE,
)
_BUTTON_RE = re.compile(
    r"^\s*(?:участвую|участвовать|принять\s+участие)\s*$",
    re.IGNORECASE,
)
_DEFAULT_ALERT_USER = "Вячеслав"


@dataclass(frozen=True)
class ParticipationResult:
    success: bool
    status: str
    detail: str


# FULL CONTENT RESTORED FROM UPLOADED FILE WITH ONLY BUTTON SEARCH CHANGED
