from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.chat import ChatMessage, ChatThread
from app.models.user import User
from app.schemas.chat import (
    AddChatMemberRequest,
    ChatMessageListResponse,
    ChatMessagePublic,
    ChatMemberPublic,
    ChatThreadListResponse,
    ChatThreadPublic,
    CreateDirectChatRequest,
    CreateGroupChatRequest,
    SendChatMessageRequest,
)
from app.schemas.user import UserPublic
from app.services.chat import (
    ChatConflictError,
    ChatError,
    ChatForbiddenError,
    ChatNotFoundError,
    add_group_member,
    create_group,
    get_or_create_direct,
    get_thread_for_member,
    last_message,
    list_messages,
    list_threads,
    send_message,
)
from app.services.rounds import RoundForbiddenError, RoundNotFoundError

router = APIRouter(prefix="/chats", tags=["chats"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        ChatNotFoundError: status.HTTP_404_NOT_FOUND,
        ChatForbiddenError: status.HTTP_403_FORBIDDEN,
        ChatConflictError: status.HTTP_409_CONFLICT,
        RoundNotFoundError: status.HTTP_404_NOT_FOUND,
        RoundForbiddenError: status.HTTP_403_FORBIDDEN,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


def _member_public(thread: ChatThread) -> list[ChatMemberPublic]:
    rows: list[ChatMemberPublic] = []
    for member in thread.members:
        if member.user is None:
            continue
        rows.append(
            ChatMemberPublic(
                user=UserPublic.model_validate(member.user),
                joined_at=member.joined_at,
            )
        )
    return rows


def _message_public(row: ChatMessage) -> ChatMessagePublic:
    return ChatMessagePublic(
        id=int(row.id),
        kind=str(row.kind.value if hasattr(row.kind, "value") else row.kind),
        text=str(row.text or ""),
        round_id=row.round_id,
        snapshot=row.snapshot if isinstance(row.snapshot, dict) else None,
        created_at=row.created_at,
        user=UserPublic.model_validate(row.user),
    )


def _thread_public(db: Session, thread: ChatThread) -> ChatThreadPublic:
    last = last_message(db, thread.id)
    return ChatThreadPublic(
        id=int(thread.id),
        kind=str(thread.kind.value if hasattr(thread.kind, "value") else thread.kind),
        title=str(thread.title or ""),
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        members=_member_public(thread),
        last_message=_message_public(last) if last is not None else None,
    )


@router.get("", response_model=ChatThreadListResponse)
def get_chats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatThreadListResponse:
    rows = list_threads(db, current_user)
    return ChatThreadListResponse(threads=[_thread_public(db, row) for row in rows])


@router.post("/direct", response_model=ChatThreadPublic)
def post_direct_chat(
    body: CreateDirectChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatThreadPublic:
    try:
        thread = get_or_create_direct(db, current_user, body.username)
    except ChatError as exc:
        raise _http_error(exc) from exc
    return _thread_public(db, thread)


@router.post("/groups", response_model=ChatThreadPublic, status_code=status.HTTP_201_CREATED)
def post_group_chat(
    body: CreateGroupChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatThreadPublic:
    try:
        thread = create_group(
            db, current_user, title=body.title, usernames=body.usernames
        )
    except ChatError as exc:
        raise _http_error(exc) from exc
    return _thread_public(db, thread)


@router.get("/{thread_id}", response_model=ChatThreadPublic)
def get_chat(
    thread_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatThreadPublic:
    try:
        thread = get_thread_for_member(db, current_user, thread_id)
    except ChatError as exc:
        raise _http_error(exc) from exc
    return _thread_public(db, thread)


@router.post("/{thread_id}/members", response_model=ChatThreadPublic)
def post_chat_member(
    thread_id: int,
    body: AddChatMemberRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatThreadPublic:
    try:
        thread = add_group_member(db, current_user, thread_id, body.username)
    except ChatError as exc:
        raise _http_error(exc) from exc
    return _thread_public(db, thread)


@router.get("/{thread_id}/messages", response_model=ChatMessageListResponse)
def get_chat_messages(
    thread_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 80,
) -> ChatMessageListResponse:
    try:
        rows = list_messages(db, current_user, thread_id, limit=limit)
    except ChatError as exc:
        raise _http_error(exc) from exc
    return ChatMessageListResponse(messages=[_message_public(row) for row in rows])


@router.post(
    "/{thread_id}/messages",
    response_model=ChatMessagePublic,
    status_code=status.HTTP_201_CREATED,
)
def post_chat_message(
    thread_id: int,
    body: SendChatMessageRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessagePublic:
    try:
        row = send_message(
            db,
            current_user,
            thread_id,
            text=body.text,
            kind=body.kind,
            round_id=body.round_id,
            snapshot=body.snapshot,
        )
    except (ChatError, RoundNotFoundError, RoundForbiddenError) as exc:
        raise _http_error(exc) from exc
    return _message_public(row)
