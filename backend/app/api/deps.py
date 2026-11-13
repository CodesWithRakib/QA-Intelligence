"""Shared API dependencies: authentication, pagination, lookups."""

import hmac
import uuid
from typing import Annotated, Any, TypeVar

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.base import Base

Db = Annotated[Session, Depends(get_db)]
ModelT = TypeVar("ModelT", bound=Base)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Every /api route requires the local bearer token (constant-time comparison)."""
    expected = get_settings().api_token.get_secret_value()
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API token",
                            headers={"WWW-Authenticate": "Bearer"})  # fmt: skip


class Paging:
    def __init__(self, limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> None:
        self.limit, self.offset = limit, offset

    def page(self, db: Session, query: Select[Any], serialise: Any) -> dict[str, Any]:
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        rows = db.scalars(query.limit(self.limit).offset(self.offset)).unique().all()
        return {
            "items": [serialise(row) for row in rows],
            "total": total,
            "limit": self.limit,
            "offset": self.offset,
        }


def get_or_404[ModelT: Base](db: Session, model: type[ModelT], entity_id: uuid.UUID) -> ModelT:
    entity = db.get(model, entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return entity


PagingDep = Annotated[Paging, Depends()]
