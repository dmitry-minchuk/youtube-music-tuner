"""User-adjustable playback and automation settings (docs/07 section 7).

Only non-secret values live here. ``pauseOnHidden`` defaults to **false**:
nothing may interrupt playback on this personal installation. Browsers report
"hidden" whenever the window is occluded by another application, so a default
of true stopped the music every time the owner switched apps. The switch
remains for anyone who wants the stricter, policy-conservative behaviour
(docs/04 section 1).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.persistence.database import get_session
from app.persistence.models import AppSetting

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

DEFAULTS: dict[str, Any] = {
    "pauseOnHidden": False,
    "defaultTemperature": 50,
    "defaultMood": "ANY",
    "volume": 80,
}


class SettingsPatch(BaseModel):
    pauseOnHidden: bool | None = None
    defaultTemperature: int | None = Field(default=None, ge=0, le=100)
    defaultMood: Literal["ANY", "FOCUS", "ENERGY", "CALM", "BACKGROUND", "REDISCOVER"] | None = None
    volume: int | None = Field(default=None, ge=0, le=100)


def read_settings(db: Session) -> dict[str, Any]:
    stored = {row.key: row.value_json.get("value") for row in db.query(AppSetting).all()}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


@router.get("")
def get_settings_endpoint(db: Session = Depends(get_session)) -> dict[str, Any]:
    return read_settings(db)


@router.patch("")
def patch_settings(body: SettingsPatch, db: Session = Depends(get_session)) -> dict[str, Any]:
    for key, value in body.model_dump(exclude_none=True).items():
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value_json={"value": value})
            db.add(row)
        else:
            row.value_json = {"value": value}
    db.flush()
    return read_settings(db)
