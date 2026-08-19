from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.status import (
    ActivityEvent,
    ActivityKind,
    LiveState,
    PlayMode,
    PrivacyMode,
    UserStatus,
)
from app.models.user import User
from app.schemas.feed import MODE_LABELS
from app.services.friends import list_friend_ids

PLAYING_STALE_AFTER = timedelta(hours=6)


class FeedError(ValueError):
    pass


class InvalidStatusError(FeedError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def mode_label(mode: PlayMode | None) -> str | None:
    if mode is None:
        return None
    return MODE_LABELS[mode]


def _show_scores(privacy: PrivacyMode) -> bool:
    return privacy == PrivacyMode.FULL


def get_or_create_status(db: Session, user: User) -> UserStatus:
    row = db.get(UserStatus, user.id)
    if row is not None:
        return row
    row = UserStatus(
        user_id=user.id,
        state=LiveState.IDLE,
        privacy=PrivacyMode.FULL,
        allow_join=True,
    )
    db.add(row)
    db.flush()
    return row


def get_own_status(db: Session, user: User) -> UserStatus:
    row = db.get(UserStatus, user.id)
    if row is not None:
        if row.user is None:
            row.user = user
        return row
    return UserStatus(
        user_id=user.id,
        state=LiveState.IDLE,
        privacy=PrivacyMode.FULL,
        allow_join=True,
        updated_at=_now(),
        user=user,
    )


def record_activity(
    db: Session,
    user: User,
    kind: ActivityKind,
    *,
    course_name: str | None = None,
    mode: PlayMode | None = None,
    hole: int | None = None,
    total: int | None = None,
    scores: list[int] | None = None,
    privacy: PrivacyMode = PrivacyMode.FULL,
    summary: str | None = None,
    payload: dict | None = None,
    commit: bool = True,
) -> ActivityEvent:
    event = ActivityEvent(
        user_id=user.id,
        kind=kind,
        summary=summary or _default_summary(user.username, kind, course_name, mode),
        course_name=course_name,
        mode=mode,
        hole=hole,
        total=total,
        scores=list(scores) if scores is not None else None,
        privacy=privacy,
        payload=payload,
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event


def _default_summary(
    username: str,
    kind: ActivityKind,
    course_name: str | None,
    mode: PlayMode | None,
) -> str:
    label = mode_label(mode)
    at_course = f" at {course_name}" if course_name else ""
    with_mode = f" a {label} round" if label else " a round"
    if kind == ActivityKind.STARTED_ROUND:
        return f"@{username} started{with_mode}{at_course}"
    if kind == ActivityKind.FINISHED_ROUND:
        return f"@{username} finished{with_mode}{at_course}"
    if kind == ActivityKind.WON_SKINS:
        return f"@{username} won skins{at_course}"
    if kind == ActivityKind.WON_CHALLENGE:
        return f"@{username} won a challenge{at_course}"
    return f"@{username} updated their status"


def update_status(
    db: Session,
    user: User,
    *,
    state: LiveState,
    mode: PlayMode | None = None,
    course_name: str | None = None,
    course_id: str | None = None,
    hole: int | None = None,
    holes_completed: int | None = None,
    num_holes: int | None = None,
    scores: list[int] | None = None,
    total: int | None = None,
    privacy: PrivacyMode = PrivacyMode.FULL,
    allow_join: bool = True,
) -> UserStatus:
    row = get_or_create_status(db, user)
    previous = row.state

    if state == LiveState.IDLE:
        row.state = LiveState.IDLE
        row.mode = None
        row.course_name = None
        row.course_id = None
        row.hole = None
        row.holes_completed = None
        row.num_holes = None
        row.scores = None
        row.total = None
        row.privacy = privacy
        row.allow_join = True
    else:
        row.state = state
        row.mode = mode
        row.course_name = course_name
        row.course_id = course_id
        row.hole = hole
        row.holes_completed = holes_completed
        row.num_holes = num_holes
        row.scores = list(scores) if scores is not None else None
        row.total = total
        row.privacy = privacy
        row.allow_join = allow_join

    row.updated_at = _now()

    if state == LiveState.PLAYING and previous != LiveState.PLAYING:
        record_activity(
            db,
            user,
            ActivityKind.STARTED_ROUND,
            course_name=course_name,
            mode=mode,
            hole=hole or 1,
            privacy=privacy,
            commit=False,
        )
    elif state == LiveState.FINISHED and previous != LiveState.FINISHED:
        record_activity(
            db,
            user,
            ActivityKind.FINISHED_ROUND,
            course_name=course_name,
            mode=mode,
            hole=hole,
            total=total,
            scores=scores,
            privacy=privacy,
            commit=False,
        )

    db.commit()
    db.refresh(row)
    return row


def _is_stale_playing(row: UserStatus, now: datetime) -> bool:
    if row.state != LiveState.PLAYING:
        return False
    updated = _aware(row.updated_at)
    if updated is None:
        return False
    return now - updated > PLAYING_STALE_AFTER


def live_view(row: UserStatus, user: User, *, viewer_is_owner: bool) -> dict:
    privacy = row.privacy
    show = viewer_is_owner or _show_scores(privacy)
    hole = row.hole
    return {
        "user": user,
        "state": row.state,
        "mode": row.mode,
        "mode_label": mode_label(row.mode),
        "course_name": row.course_name,
        "course_id": row.course_id,
        "hole": hole,
        "holes_completed": row.holes_completed,
        "num_holes": row.num_holes,
        "privacy": privacy,
        "show_scores": show,
        "scores": list(row.scores or []) if show else [],
        "total": row.total if show else None,
        "allow_join": bool(row.allow_join),
        "updated_at": row.updated_at,
    }


def activity_view(event: ActivityEvent, user: User, *, viewer_is_owner: bool) -> dict:
    show = viewer_is_owner or _show_scores(event.privacy)
    return {
        "id": event.id,
        "user": user,
        "kind": event.kind,
        "summary": event.summary,
        "course_name": event.course_name,
        "mode": event.mode,
        "mode_label": mode_label(event.mode),
        "hole": event.hole,
        "show_scores": show,
        "scores": list(event.scores or []) if show else [],
        "total": event.total if show else None,
        "created_at": event.created_at,
        "payload": event.payload if show else None,
    }


def build_friends_feed(
    db: Session,
    actor: User,
    *,
    activity_limit: int = 30,
    since: datetime | None = None,
) -> tuple[list[dict], list[dict], datetime]:
    now = _now()
    friend_ids = list_friend_ids(db, actor)
    live: list[dict] = []
    activity: list[dict] = []
    if not friend_ids:
        return live, activity, now

    status_rows = list(
        db.scalars(
            select(UserStatus)
            .options(selectinload(UserStatus.user))
            .where(
                UserStatus.user_id.in_(friend_ids),
                UserStatus.state == LiveState.PLAYING,
            )
            .order_by(UserStatus.updated_at.desc())
        ).all()
    )
    for row in status_rows:
        if _is_stale_playing(row, now):
            continue
        live.append(live_view(row, row.user, viewer_is_owner=False))

    event_q = (
        select(ActivityEvent)
        .options(selectinload(ActivityEvent.user))
        .where(ActivityEvent.user_id.in_(friend_ids))
        .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
        .limit(activity_limit)
    )
    if since is not None:
        event_q = event_q.where(ActivityEvent.created_at > since)

    for event in db.scalars(event_q).all():
        activity.append(activity_view(event, event.user, viewer_is_owner=False))

    return live, activity, now
