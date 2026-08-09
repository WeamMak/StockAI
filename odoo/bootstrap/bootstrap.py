# ruff: noqa: E402, F821
"""Finite, idempotent Odoo-shell bootstrap for the StockAI integration key."""

from __future__ import annotations

import calendar
import datetime
import json
import os
import sys

from odoo.fields import Command

sys.path.insert(0, "/opt/stockai")
from sinks import sink_from_environment

GROUP_XML_ID = "stockai_procurement.group_stockai_procurement_integration"
DEFAULT_LOGIN = "stockai-integration@example.invalid"
DEFAULT_KEY_NAME = "stockai-procurement-integration"
DEFAULT_EXPIRY_DAYS = 30


def _boolean_environment(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise RuntimeError(f"bootstrap setting must be true or false: {name}")
    return normalized == "true"


def _add_calendar_months(value: datetime.datetime, months: int) -> datetime.datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _expiration_date() -> datetime.datetime:
    raw_days = os.environ.get(
        "STOCKAI_ODOO_BOOTSTRAP_KEY_EXPIRY_DAYS", str(DEFAULT_EXPIRY_DAYS)
    )
    try:
        days = int(raw_days)
    except ValueError as exc:
        raise RuntimeError("bootstrap key expiry days must be an integer") from exc
    if days < 1:
        raise RuntimeError("bootstrap key expiry must be at least one day")
    now = datetime.datetime.now()
    expiration = now + datetime.timedelta(days=days)
    if expiration > _add_calendar_months(now, 3):
        raise RuntimeError("bootstrap key expiry cannot exceed three calendar months")
    return expiration


def _active_keys(user_id: int):
    return (
        env["res.users.apikeys"]
        .sudo()
        .search(  # noqa: F821
            [
                ("user_id", "=", user_id),
                "|",
                ("expiration_date", "=", False),
                ("expiration_date", ">=", datetime.datetime.now()),
            ]
        )
    )


def _find_or_create_user(login: str):
    group = env.ref(GROUP_XML_ID)  # noqa: F821
    users = env["res.users"].sudo()  # noqa: F821
    matches = users.with_context(active_test=False).search([("login", "=", login)])
    if len(matches) > 1:
        raise RuntimeError("bootstrap found duplicate integration users")
    created = not matches
    if created:
        matches = users.create(
            {
                "name": "StockAI Procurement Integration",
                "login": login,
                "email": login,
                "active": True,
                "group_ids": [Command.set([group.id])],
            }
        )
    user = matches.ensure_one()
    changes = {}
    if not user.active:
        changes["active"] = True
    if set(user.group_ids.ids) != {group.id}:
        changes["group_ids"] = [Command.set([group.id])]
    if changes:
        user.write(changes)
    return user, created


def _verify_key(raw_key: str, user_id: int) -> None:
    authenticated_user_id = env[  # noqa: F821
        "res.users.apikeys"
    ]._check_credentials(scope="rpc", key=raw_key)
    if authenticated_user_id != user_id:
        raise RuntimeError(
            "the bootstrap key does not authenticate its integration user"
        )


login = os.environ.get("STOCKAI_ODOO_BOOTSTRAP_LOGIN", DEFAULT_LOGIN).strip()
key_name = os.environ.get("STOCKAI_ODOO_BOOTSTRAP_KEY_NAME", DEFAULT_KEY_NAME).strip()
if not login or not key_name:
    raise RuntimeError("bootstrap login and key name cannot be empty")
rotate = _boolean_environment("STOCKAI_ODOO_BOOTSTRAP_ROTATE")
secret_sink = sink_from_environment()
user, user_created = _find_or_create_user(login)
active_keys = _active_keys(user.id)
stored_key = secret_sink.read()

if stored_key is not None:
    _verify_key(stored_key, user.id)
    if len(active_keys) != 1 or active_keys.name != key_name:
        raise RuntimeError(
            "bootstrap expected exactly one recoverable active named key"
        )
elif active_keys:
    raise RuntimeError(
        "an active integration key exists but its raw value is unavailable"
    )

status = "existing"
if stored_key is None or rotate:
    expiration = _expiration_date()
    previous_keys = active_keys
    new_key = (
        env["res.users.apikeys"]  # noqa: F821
        .with_user(user)
        ._generate(scope="rpc", name=key_name, expiration_date=expiration)
    )
    _verify_key(new_key, user.id)
    secret_sink.write(new_key)
    env.cr.commit()  # noqa: F821 - replacement is durable before revocation
    if previous_keys:
        previous_keys.sudo()._remove()
        env.cr.commit()  # noqa: F821
    status = "rotated" if stored_key is not None else "created"
    stored_key = new_key
else:
    if user_created:
        raise RuntimeError("a newly created user unexpectedly had an existing key")
    env.cr.commit()  # noqa: F821 - persist any group reconciliation

active_keys = _active_keys(user.id)
_verify_key(stored_key, user.id)
if len(active_keys) != 1 or active_keys.name != key_name:
    raise RuntimeError("bootstrap did not finish with exactly one active named key")

print(
    json.dumps(
        {
            "active_named_key_count": len(active_keys),
            "direct_group_count": len(user.group_ids),
            "expiration_date": str(active_keys.expiration_date),
            "sink": secret_sink.kind,
            "status": status,
            "user_id": user.id,
        },
        sort_keys=True,
    )
)
