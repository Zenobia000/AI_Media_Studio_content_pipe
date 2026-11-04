"""Errors the admin API answers as 400: raised by the engine, turned into responses by the API layer."""

from __future__ import annotations


class Invalid(Exception):
    """A request body or parameter that failed validation (a ZodError in the TypeScript): a 400 naming what is wrong."""


class BadRequest(Exception):
    """An error the TypeScript raised with statusCode 400: also a 400."""
