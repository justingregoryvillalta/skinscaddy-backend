from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.chat import ChatMember, ChatMessage, ChatMessageKind, ChatThread, ChatThreadKind
from app.models.round import Round
from app.models.user import User
from app.services.friends import are_friends
from app.services.rounds import get_owned_round
from app.services.users import get_user_by_username


class ChatError(ValueError):
    pass


class ChatNotFoundError(ChatError):
    pass


class ChatForbiddenError(ChatError):
    pass


class ChatConflictError(ChatError):
    pass


_THREAD_LOAD = (
    selectinload(ChatThread.members).selectinload(ChatMember.user),
    selectinload(ChatThread.created_by),
)
_MESSAGE_LOAD = (selectinload(ChatMessage.user),)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pair_key(user_a_id: int, user_b_id: int) -> str:
    lo, hi = sorted((int(user_a_id), int(user_b_id)))
    return f"{lo}:{hi}"


def _require_member(db: Session, thread: ChatThread, actor: User) -> ChatMember:
    member = next((row for row in thread.members if row.user_id == actor.id), None)
    if member is None:
        raise ChatForbiddenError("Only members of this chat can open it.")
    return member


def _load_thread(db: Session, thread_id: int) -> ChatThread | None:
    return db.scalar(
        select(ChatThread).options(*_THREAD_LOAD).where(ChatThread.id == int(thread_id))
    )


def get_thread_for_member(db: Session, actor: User, thread_id: int) -> ChatThread:
    thread = _load_thread(db, thread_id)
    if thread is None:
        raise ChatNotFoundError("Chat not found.")
    _require_member(db, thread, actor)
    return thread


def list_threads(db: Session, actor: User) -> list[ChatThread]:
    ids = list(
        db.scalars(select(ChatMember.thread_id).where(ChatMember.user_id == actor.id)).all()
    )
    if not ids:
        return []
    rows = list(
        db.scalars(
            select(ChatThread)
            .options(*_THREAD_LOAD)
            .where(ChatThread.id.in_(ids))
            .order_by(ChatThread.updated_at.desc())
        ).all()
    )
    return rows


def last_message(db: Session, thread_id: int) -> ChatMessage | None:
    return db.scalar(
        select(ChatMessage)
        .options(*_MESSAGE_LOAD)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(1)
    )


def get_or_create_direct(db: Session, actor: User, username: str) -> ChatThread:
    other = get_user_by_username(db, (username or "").strip().lstrip("@"))
    if other is None:
        raise ChatNotFoundError("No player with that username.")
    if other.id == actor.id:
        raise ChatError("You cannot open a chat with yourself.")
    if not are_friends(db, actor.id, other.id):
        raise ChatForbiddenError("You can only chat with friends.")
    key = _pair_key(actor.id, other.id)
    existing = db.scalar(select(ChatThread).options(*_THREAD_LOAD).where(ChatThread.pair_key == key))
    if existing is not None:
        return existing
    thread = ChatThread(
        kind=ChatThreadKind.DIRECT,
        title="",
        pair_key=key,
        created_by_id=actor.id,
        updated_at=_now(),
    )
    db.add(thread)
    db.flush()
    db.add(ChatMember(thread_id=thread.id, user_id=actor.id))
    db.add(ChatMember(thread_id=thread.id, user_id=other.id))
    db.commit()
    return get_thread_for_member(db, actor, thread.id)


def create_group(
    db: Session,
    actor: User,
    *,
    title: str,
    usernames: list[str],
) -> ChatThread:
    names = [n for n in usernames if n.strip().lower() != actor.username.lower()]
    others: list[User] = []
    for name in names:
        user = get_user_by_username(db, name)
        if user is None:
            raise ChatNotFoundError(f"No player named {name}.")
        if not are_friends(db, actor.id, user.id):
            raise ChatForbiddenError(f"@{user.username} is not on your friends list.")
        others.append(user)
    if not others:
        raise ChatError("Pick at least one friend for the group.")
    thread = ChatThread(
        kind=ChatThreadKind.GROUP,
        title=title.strip(),
        pair_key=None,
        created_by_id=actor.id,
        updated_at=_now(),
    )
    db.add(thread)
    db.flush()
    db.add(ChatMember(thread_id=thread.id, user_id=actor.id))
    for user in others:
        db.add(ChatMember(thread_id=thread.id, user_id=user.id))
    db.commit()
    return get_thread_for_member(db, actor, thread.id)


def add_group_member(db: Session, actor: User, thread_id: int, username: str) -> ChatThread:
    thread = get_thread_for_member(db, actor, thread_id)
    if thread.kind != ChatThreadKind.GROUP:
        raise ChatError("You can only add people to a group chat.")
    other = get_user_by_username(db, username)
    if other is None:
        raise ChatNotFoundError("No player with that username.")
    if other.id == actor.id:
        raise ChatError("You are already in this chat.")
    if not are_friends(db, actor.id, other.id):
        raise ChatForbiddenError("You can only add friends to a group chat.")
    if any(row.user_id == other.id for row in thread.members):
        raise ChatConflictError("That friend is already in the chat.")
    db.add(ChatMember(thread_id=thread.id, user_id=other.id))
    thread.updated_at = _now()
    db.commit()
    return get_thread_for_member(db, actor, thread.id)


def list_messages(db: Session, actor: User, thread_id: int, *, limit: int = 80) -> list[ChatMessage]:
    get_thread_for_member(db, actor, thread_id)
    cap = max(1, min(int(limit or 80), 200))
    total = db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread_id)
    ) or 0
    offset = max(0, int(total) - cap)
    rows = list(
        db.scalars(
            select(ChatMessage)
            .options(*_MESSAGE_LOAD)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .offset(offset)
            .limit(cap)
        ).all()
    )
    return rows


def _clean_snapshot(raw: dict[str, Any] | None, round_obj: Round | None) -> dict[str, Any]:
    src = dict(raw or {})
    if round_obj is not None:
        src.setdefault("round_id", int(round_obj.id))
        src.setdefault("course", round_obj.course_name)
        src.setdefault("holes", int(round_obj.num_holes))
        src.setdefault("score", int(round_obj.total))
        src.setdefault("date", round_obj.created_at.date().isoformat() if round_obj.created_at else "")
        src.setdefault("players", [round_obj.user.username] if round_obj.user else [])
        src.setdefault("scores", list(round_obj.scores or []))
        src.setdefault("pars", list(round_obj.pars or []) if round_obj.pars else [])
    allowed = {
        "round_id",
        "round_key",
        "course",
        "date",
        "score",
        "holes",
        "skins_result",
        "players",
        "scores",
        "pars",
        "summary",
        "chips",
    }
    out: dict[str, Any] = {}
    for key, value in src.items():
        if key not in allowed:
            continue
        out[key] = value
    course = str(out.get("course") or "Course")
    holes = out.get("holes") or ""
    score = out.get("score")
    skins = str(out.get("skins_result") or "").strip()
    players = out.get("players") if isinstance(out.get("players"), list) else []
    bits = [course]
    if holes:
        bits.append(f"{holes} holes")
    if score is not None and str(score) != "":
        bits.append(f"score {score}")
    if skins:
        bits.append(skins)
    if players:
        bits.append(", ".join(str(p) for p in players[:6]))
    out["summary"] = str(out.get("summary") or " · ".join(bits))
    return out


def send_message(
    db: Session,
    actor: User,
    thread_id: int,
    *,
    text: str | None,
    kind: str = "text",
    round_id: int | None = None,
    snapshot: dict[str, Any] | None = None,
) -> ChatMessage:
    thread = get_thread_for_member(db, actor, thread_id)
    msg_kind = ChatMessageKind.ROUND if kind == "round" else ChatMessageKind.TEXT
    round_obj = None
    snap = None
    body = (text or "").strip()
    if msg_kind == ChatMessageKind.ROUND:
        if round_id is not None:
            round_obj = get_owned_round(db, actor, int(round_id))
        snap = _clean_snapshot(snapshot, round_obj)
        body = body or str(snap.get("summary") or "Round")
    elif not body:
        raise ChatError("Type a message first.")
    if len(body) > 2000:
        body = body[:2000]
    record = ChatMessage(
        thread_id=thread.id,
        user_id=actor.id,
        kind=msg_kind,
        text=body,
        round_id=int(round_obj.id) if round_obj is not None else (
            int(round_id) if round_id is not None else None
        ),
        snapshot=snap,
        created_at=_now(),
    )
    db.add(record)
    thread.updated_at = _now()
    db.commit()
    db.refresh(record)
    loaded = db.scalar(
        select(ChatMessage).options(*_MESSAGE_LOAD).where(ChatMessage.id == record.id)
    )
    return loaded or record
