"""Modelos de entrada/salida de la API (validación con Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El mensaje no puede estar vacío.")
        return value.strip()


class CartIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    action: Literal["add", "inc", "dec", "remove", "clear"]
    product_id: str | None = Field(default=None, max_length=40)
    uid: str | None = Field(default=None, max_length=40)
    qty: int | None = Field(default=None, ge=1, le=20)

    @field_validator("product_id")
    @classmethod
    def valid_product(cls, value: str | None) -> str | None:
        return value


class ResetIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)


class StockIn(BaseModel):
    available: bool
