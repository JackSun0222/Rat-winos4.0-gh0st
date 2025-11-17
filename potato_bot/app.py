"""Simple points bot service for Potato-style chat integrations.

This FastAPI application exposes endpoints for:
- Admin web control panel actions to adjust user points.
- Admin @bot commands parsed from chat messages.
- Redeeming points for rewards while preserving a ledger.

Data is stored in an on-disk SQLite database to keep deployment light.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import datetime
import sqlite3

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).with_name("potato_points.db")

app = FastAPI(
    title="Potato Points Bot",
    description=(
        "A lightweight points tracker for Potato groups. "
        "Admins can adjust balances from the control panel or by @mentioning the bot."
    ),
    version="0.1.0",
)


class AdjustRequest(BaseModel):
    delta: int = Field(..., description="Positive to add points, negative to deduct")
    reason: str = Field(..., description="Why the balance changed")
    actor: Optional[str] = Field(None, description="Who initiated the change")


class BotCommand(BaseModel):
    sender: str = Field(..., description="Admin user executing the command")
    text: str = Field(..., description="Raw chat message including the command")
    is_admin: bool = Field(False, description="Whether the sender has admin rights")


class RedeemRequest(BaseModel):
    user_id: str
    amount: int
    purpose: str
    actor: Optional[str] = Field(None, description="Name of the person approving the redemption")


class Transaction(BaseModel):
    user_id: str
    delta: int
    reason: str
    actor: str
    kind: str
    created_at: str


class PointsResponse(BaseModel):
    user_id: str
    points: int
    ledger: List[Transaction]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )


def _ensure_user(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))


def _current_points(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        "SELECT points FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def _record_transaction(
    conn: sqlite3.Connection,
    user_id: str,
    delta: int,
    reason: str,
    actor: str,
    kind: str,
) -> int:
    now = datetime.datetime.utcnow().isoformat()
    _ensure_user(conn, user_id)
    conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (delta, user_id))
    new_total = _current_points(conn, user_id)
    conn.execute(
        """
        INSERT INTO transactions (user_id, delta, reason, actor, kind, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, delta, reason, actor, kind, now),
    )
    conn.commit()
    return new_total


def _serialize_ledger(rows: List[sqlite3.Row]) -> List[Transaction]:
    return [
        Transaction(
            user_id=row["user_id"],
            delta=row["delta"],
            reason=row["reason"],
            actor=row["actor"],
            kind=row["kind"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@app.on_event("startup")
def on_startup() -> None:
    _init_db()


@app.get("/users/{user_id}", response_model=PointsResponse)
def get_user_points(user_id: str) -> PointsResponse:
    with _get_conn() as conn:
        ledger = conn.execute(
            """
            SELECT user_id, delta, reason, actor, kind, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()
        return PointsResponse(
            user_id=user_id,
            points=_current_points(conn, user_id),
            ledger=_serialize_ledger(ledger),
        )


@app.post("/admin/users/{user_id}/adjust", response_model=PointsResponse)
def adjust_points(user_id: str, request: AdjustRequest) -> PointsResponse:
    with _get_conn() as conn:
        new_total = _record_transaction(
            conn,
            user_id,
            request.delta,
            request.reason,
            request.actor or "control-panel",
            "manual",
        )
        ledger = conn.execute(
            """
            SELECT user_id, delta, reason, actor, kind, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()
        return PointsResponse(user_id=user_id, points=new_total, ledger=_serialize_ledger(ledger))


def _parse_bot_command(text: str) -> tuple[str, str, int, str]:
    """Parse a Potato-style @bot command.

    Expected formats:
    - "@bot add <user_id> <amount> <reason...>"
    - "@bot deduct <user_id> <amount> <reason...>"
    - "@bot redeem <user_id> <amount> <purpose...>"
    Returns (action, user_id, amount, reason/purpose).
    """

    parts = text.strip().split()
    if len(parts) < 5:
        raise ValueError("命令格式: @bot <add|deduct|redeem> <用户> <积分> <原因>")
    mention, action, user_id, raw_amount, *rest = parts
    if not mention.startswith("@"):
        raise ValueError("消息需要@机器人触发")

    try:
        amount = int(raw_amount)
    except ValueError as exc:
        raise ValueError("积分必须是数字") from exc

    if amount <= 0:
        raise ValueError("积分必须大于0")

    reason = " ".join(rest)
    return action.lower(), user_id, amount, reason


@app.post("/bot/command", response_model=PointsResponse)
def handle_bot_command(command: BotCommand) -> PointsResponse:
    if not command.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以执行积分命令")

    try:
        action, user_id, amount, reason = _parse_bot_command(command.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    delta = amount if action in {"add", "give", "award"} else -amount
    kind = "bot-redeem" if action == "redeem" else "bot-admin"

    with _get_conn() as conn:
        current = _current_points(conn, user_id)
        if action in {"deduct", "redeem"} and current < amount:
            raise HTTPException(status_code=400, detail="积分不足")

        new_total = _record_transaction(
            conn,
            user_id,
            delta,
            reason,
            command.sender,
            kind,
        )
        ledger = conn.execute(
            """
            SELECT user_id, delta, reason, actor, kind, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()
        return PointsResponse(user_id=user_id, points=new_total, ledger=_serialize_ledger(ledger))


@app.post("/redeem", response_model=PointsResponse)
def redeem_points(request: RedeemRequest) -> PointsResponse:
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="兑换积分必须大于0")

    with _get_conn() as conn:
        current = _current_points(conn, request.user_id)
        if current < request.amount:
            raise HTTPException(status_code=400, detail="积分不足")

        new_total = _record_transaction(
            conn,
            request.user_id,
            -request.amount,
            request.purpose,
            request.actor or "user-redeem",
            "redeem",
        )
        ledger = conn.execute(
            """
            SELECT user_id, delta, reason, actor, kind, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (request.user_id,),
        ).fetchall()
        return PointsResponse(user_id=request.user_id, points=new_total, ledger=_serialize_ledger(ledger))


@app.get("/ledger", response_model=List[Transaction])
def list_ledger(limit: int = 50) -> List[Transaction]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, delta, reason, actor, kind, created_at
            FROM transactions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return _serialize_ledger(rows)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
