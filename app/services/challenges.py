from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.challenge import (
    Challenge,
    ChallengePlayer,
    ChallengePlayerRole,
    ChallengePlayerStatus,
    ChallengeStatus,
)
from app.models.user import User
from app.models.wallet import TokenSource
from app.services.friends import UserNotFoundError, are_friends
from app.services.rounds import RoundNotFoundError, get_owned_round, get_round
from app.services.users import get_user_by_id, get_user_by_username
from app.models.status import ActivityKind
from app.services.feed import record_activity
from app.services.wallet import (
    InsufficientTokensError,
    credit_tokens,
    debit_tokens,
)

MAX_OPPONENTS = 3
_TERMINAL = {
    ChallengeStatus.COMPLETED,
    ChallengeStatus.EXPIRED,
    ChallengeStatus.FORFEITED,
}


class ChallengeError(ValueError):
    pass


class ChallengeNotFoundError(ChallengeError):
    pass


class ChallengeForbiddenError(ChallengeError):
    pass


class ChallengeStateError(ChallengeError):
    pass


class NotFriendsError(ChallengeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ref(challenge_id: int) -> str:
    return f"challenge:{challenge_id}"


def _load_options():
    from app.models.round import Round

    return (
        selectinload(Challenge.creator),
        selectinload(Challenge.source_round).selectinload(Round.user),
        selectinload(Challenge.players).selectinload(ChallengePlayer.user),
    )


def get_challenge(db: Session, challenge_id: int) -> Challenge | None:
    return db.scalar(
        select(Challenge).options(*_load_options()).where(Challenge.id == challenge_id)
    )


def _require_member(challenge: Challenge, actor: User) -> ChallengePlayer:
    for player in challenge.players:
        if player.user_id == actor.id:
            return player
    raise ChallengeForbiddenError("You are not part of this challenge.")


def _player_for(challenge: Challenge, user_id: int) -> ChallengePlayer | None:
    return next((p for p in challenge.players if p.user_id == user_id), None)


def create_challenge(
    db: Session,
    actor: User,
    *,
    usernames: list[str],
    round_id: int,
    wager_amount: int,
    weeks: int,
) -> Challenge:
    source = get_owned_round(db, actor, round_id)
    if len(list(source.scores or [])) < source.num_holes:
        raise ChallengeStateError("Finish the full card before sending a challenge.")

    opponents: list[User] = []
    seen: set[int] = set()
    for name in usernames:
        user = get_user_by_username(db, name)
        if user is None:
            raise UserNotFoundError(f"User '{name}' not found.")
        if user.id == actor.id:
            raise ChallengeStateError("You cannot challenge yourself.")
        if user.id in seen:
            continue
        if not are_friends(db, actor.id, user.id):
            raise NotFriendsError(f"@{user.username} is not on your friends list.")
        seen.add(user.id)
        opponents.append(user)

    if not opponents:
        raise ChallengeStateError("Pick 1 to 3 friends to challenge.")
    if len(opponents) > MAX_OPPONENTS:
        raise ChallengeStateError(f"You can challenge at most {MAX_OPPONENTS} friends.")
    if wager_amount > 0 and actor.token_balance < wager_amount:
        raise InsufficientTokensError("You do not have enough tokens for this wager.")

    now = _now()
    challenge = Challenge(
        creator_id=actor.id,
        source_round_id=source.id,
        course_name=source.course_name,
        num_holes=source.num_holes,
        wager_amount=wager_amount,
        duration_weeks=weeks,
        deadline=now + timedelta(weeks=weeks),
        status=ChallengeStatus.PENDING,
        pot_amount=0,
    )
    db.add(challenge)
    db.flush()

    host_scores = [int(s) for s in source.scores]
    db.add(
        ChallengePlayer(
            challenge_id=challenge.id,
            user_id=actor.id,
            role=ChallengePlayerRole.HOST,
            status=ChallengePlayerStatus.COMPLETED,
            scores=host_scores,
            total=int(source.total),
            escrowed=False,
            escrow_amount=0,
            finished_at=now,
        )
    )
    for opp in opponents:
        db.add(
            ChallengePlayer(
                challenge_id=challenge.id,
                user_id=opp.id,
                role=ChallengePlayerRole.OPPONENT,
                status=ChallengePlayerStatus.PENDING,
                scores=[],
                total=None,
                escrowed=False,
                escrow_amount=0,
            )
        )
    db.commit()
    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    try:
        from app.services.honor import recompute_and_save

        recompute_and_save(db, actor)
        for opp in opponents:
            recompute_and_save(db, opp)
    except Exception:
        pass
    return loaded


def join_round_challenge(
    db: Session,
    actor: User,
    *,
    round_id: int,
    wager_amount: int,
    weeks: int,
) -> Challenge:
    """Used by POST /challenges/join-round (already imported from the API)."""
    source = get_round(db, round_id)
    if source is None:
        raise RoundNotFoundError("Round not found.")
    if source.user_id == actor.id:
        raise ChallengeStateError("You cannot join your own round.")
    owner = source.user or get_user_by_id(db, source.user_id)
    if owner is None:
        raise ChallengeStateError("Round owner is missing.")
    if not are_friends(db, actor.id, owner.id):
        raise NotFriendsError(f"@{owner.username} is not on your friends list.")
    if len(list(source.scores or [])) < int(source.num_holes):
        raise ChallengeStateError("That round is incomplete.")

    amount = int(wager_amount)
    if amount > 0:
        if int(owner.token_balance) < amount:
            raise InsufficientTokensError("Host no longer has enough tokens for this wager.")
        if int(actor.token_balance) < amount:
            raise InsufficientTokensError("You do not have enough tokens for this wager.")

    now = _now()
    challenge = Challenge(
        creator_id=owner.id,
        source_round_id=source.id,
        course_name=source.course_name,
        num_holes=source.num_holes,
        wager_amount=amount,
        duration_weeks=weeks,
        deadline=now + timedelta(weeks=weeks),
        status=ChallengeStatus.ACTIVE,
        pot_amount=0,
    )
    db.add(challenge)
    db.flush()

    host_scores = [int(s) for s in source.scores]
    db.add(
        ChallengePlayer(
            challenge_id=challenge.id,
            user_id=owner.id,
            role=ChallengePlayerRole.HOST,
            status=ChallengePlayerStatus.COMPLETED,
            scores=host_scores,
            total=int(source.total),
            escrowed=False,
            escrow_amount=0,
            finished_at=now,
        )
    )
    db.add(
        ChallengePlayer(
            challenge_id=challenge.id,
            user_id=actor.id,
            role=ChallengePlayerRole.OPPONENT,
            status=ChallengePlayerStatus.ACCEPTED,
            scores=[],
            total=None,
            escrowed=False,
            escrow_amount=0,
            accepted_at=now,
        )
    )
    db.flush()

    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    host = next(p for p in loaded.players if p.role == ChallengePlayerRole.HOST)
    opponent = next(p for p in loaded.players if p.role == ChallengePlayerRole.OPPONENT)
    if amount > 0:
        _escrow_player(db, loaded, host)
        _escrow_player(db, loaded, opponent)
    db.commit()
    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    return loaded


def _escrow_player(db: Session, challenge: Challenge, player: ChallengePlayer) -> None:
    amount = int(challenge.wager_amount)
    if amount <= 0 or player.escrowed:
        return
    user = player.user or get_user_by_id(db, player.user_id)
    if user is None:
        raise ChallengeStateError("Challenge player is missing.")
    debit_tokens(
        db,
        user,
        amount=amount,
        source=TokenSource.WAGER,
        reason=f"Challenge #{challenge.id} wager escrow",
        reference=_ref(challenge.id),
        commit=False,
    )
    player.escrowed = True
    player.escrow_amount = amount
    challenge.pot_amount = int(challenge.pot_amount) + amount
    if player.user is not None:
        player.user.token_balance = user.token_balance


def accept_challenge(db: Session, actor: User, challenge_id: int) -> Challenge:
    challenge = _refresh_if_due(db, _require_challenge(db, challenge_id))
    player = _require_member(challenge, actor)
    if player.role != ChallengePlayerRole.OPPONENT:
        raise ChallengeForbiddenError("Only an invited friend can accept this challenge.")
    if player.status != ChallengePlayerStatus.PENDING:
        raise ChallengeStateError("Challenge is not open for acceptance.")
    if challenge.status in _TERMINAL:
        raise ChallengeStateError("Challenge is no longer open.")
    if _deadline_passed(challenge):
        challenge = _settle(db, challenge)
        raise ChallengeStateError("Challenge expired before accept.")

    host = next(p for p in challenge.players if p.role == ChallengePlayerRole.HOST)
    if challenge.wager_amount > 0:
        host_user = host.user or get_user_by_id(db, host.user_id)
        if host_user is None:
            raise ChallengeStateError("Host is missing.")
        if not host.escrowed and host_user.token_balance < challenge.wager_amount:
            raise InsufficientTokensError("Host no longer has enough tokens for this wager.")
        if actor.token_balance < challenge.wager_amount:
            raise InsufficientTokensError("You do not have enough tokens for this wager.")
        _escrow_player(db, challenge, host)
        _escrow_player(db, challenge, player)

    now = _now()
    player.status = ChallengePlayerStatus.ACCEPTED
    player.accepted_at = now
    if challenge.status == ChallengeStatus.PENDING:
        challenge.status = ChallengeStatus.ACTIVE
    challenge.updated_at = now
    db.commit()
    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    return loaded


def decline_challenge(db: Session, actor: User, challenge_id: int) -> Challenge:
    challenge = _refresh_if_due(db, _require_challenge(db, challenge_id))
    player = _require_member(challenge, actor)
    if player.role != ChallengePlayerRole.OPPONENT:
        raise ChallengeForbiddenError("Only an invited friend can decline this challenge.")
    if player.status != ChallengePlayerStatus.PENDING:
        raise ChallengeStateError("Challenge is not pending.")
    if challenge.status in _TERMINAL:
        raise ChallengeStateError("Challenge is no longer open.")

    player.status = ChallengePlayerStatus.DECLINED
    challenge.updated_at = _now()
    opponents = [p for p in challenge.players if p.role == ChallengePlayerRole.OPPONENT]
    if opponents and all(p.status == ChallengePlayerStatus.DECLINED for p in opponents):
        challenge.status = ChallengeStatus.EXPIRED
        challenge.settled_at = _now()
        challenge.result = {
            "kind": "expired",
            "reason": "all_declined",
            "winner_ids": [],
            "payouts": [],
        }
    db.commit()
    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    return loaded


def submit_scores(
    db: Session,
    actor: User,
    challenge_id: int,
    *,
    strokes: int | None = None,
    scores: list[int] | None = None,
) -> Challenge:
    challenge = _refresh_if_due(db, _require_challenge(db, challenge_id))
    player = _require_member(challenge, actor)
    if player.role != ChallengePlayerRole.OPPONENT:
        raise ChallengeForbiddenError("Only an invited friend can post scores.")
    if player.status not in {ChallengePlayerStatus.ACCEPTED, ChallengePlayerStatus.COMPLETED}:
        raise ChallengeStateError("Accept the challenge before posting scores.")
    if challenge.status in _TERMINAL:
        raise ChallengeStateError("Challenge is closed.")
    if player.status == ChallengePlayerStatus.COMPLETED:
        raise ChallengeStateError("Round already complete.")

    holes = int(challenge.num_holes)
    current = [int(s) for s in (player.scores or [])]
    if scores is not None:
        if len(scores) != holes:
            raise ChallengeStateError(f"scores must contain exactly {holes} holes.")
        if current:
            raise ChallengeStateError("Scores already started; submit one hole at a time.")
        current = list(scores)
    elif strokes is not None:
        if len(current) >= holes:
            raise ChallengeStateError("Round already complete.")
        current.append(int(strokes))
    else:
        raise ChallengeStateError("Provide strokes for the next hole or a full scores list.")

    player.scores = current
    flag_modified(player, "scores")
    if len(current) >= holes:
        player.scores = current[:holes]
        player.total = sum(player.scores)
        player.status = ChallengePlayerStatus.COMPLETED
        player.finished_at = _now()
    challenge.updated_at = _now()

    if _ready_to_complete(challenge):
        challenge = _settle(db, challenge)
    else:
        db.commit()
        loaded = get_challenge(db, challenge.id)
        assert loaded is not None
        return loaded
    return challenge


def settle_challenge(db: Session, actor: User, challenge_id: int) -> Challenge:
    challenge = _require_challenge(db, challenge_id)
    _require_member(challenge, actor)
    return _settle(db, challenge)


def list_history(db: Session, actor: User) -> list[Challenge]:
    _refresh_user_open(db, actor)
    return _query_for_user(db, actor)


def list_incoming(db: Session, actor: User) -> list[Challenge]:
    _refresh_user_open(db, actor)
    rows = _query_for_user(db, actor)
    out: list[Challenge] = []
    for challenge in rows:
        player = _player_for(challenge, actor.id)
        if (
            player
            and player.role == ChallengePlayerRole.OPPONENT
            and player.status == ChallengePlayerStatus.PENDING
            and challenge.status in {ChallengeStatus.PENDING, ChallengeStatus.ACTIVE}
        ):
            out.append(challenge)
    return out


def list_outgoing(db: Session, actor: User) -> list[Challenge]:
    _refresh_user_open(db, actor)
    return [
        c
        for c in _query_for_user(db, actor)
        if c.creator_id == actor.id
        and c.status in {ChallengeStatus.PENDING, ChallengeStatus.ACTIVE}
    ]


def get_visible_challenge(db: Session, actor: User, challenge_id: int) -> Challenge:
    challenge = _refresh_if_due(db, _require_challenge(db, challenge_id))
    _require_member(challenge, actor)
    return challenge


def _require_challenge(db: Session, challenge_id: int) -> Challenge:
    challenge = get_challenge(db, challenge_id)
    if challenge is None:
        raise ChallengeNotFoundError("Challenge not found.")
    return challenge


def _challenge_ids_for_user(user_id: int):
    return select(ChallengePlayer.challenge_id).where(
        ChallengePlayer.user_id == int(user_id)
    )


def _query_for_user(db: Session, actor: User) -> list[Challenge]:
    return list(
        db.scalars(
            select(Challenge)
            .options(*_load_options())
            .where(Challenge.id.in_(_challenge_ids_for_user(actor.id)))
            .order_by(Challenge.created_at.desc(), Challenge.id.desc())
        ).all()
    )


def _deadline_passed(challenge: Challenge, now: datetime | None = None) -> bool:
    when = now or _now()
    deadline = challenge.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return when >= deadline


def _refresh_if_due(db: Session, challenge: Challenge) -> Challenge:
    if challenge.status not in _TERMINAL and _deadline_passed(challenge):
        return _settle(db, challenge)
    return challenge


def _refresh_user_open(db: Session, actor: User) -> None:
    now = _now()
    open_rows = list(
        db.scalars(
            select(Challenge)
            .options(*_load_options())
            .where(
                Challenge.id.in_(_challenge_ids_for_user(actor.id)),
                Challenge.status.in_((ChallengeStatus.PENDING, ChallengeStatus.ACTIVE)),
                Challenge.deadline <= now,
            )
        ).all()
    )
    for challenge in open_rows:
        _settle(db, challenge)


def _ready_to_complete(challenge: Challenge) -> bool:
    opponents = [p for p in challenge.players if p.role == ChallengePlayerRole.OPPONENT]
    if not opponents:
        return False
    if any(p.status == ChallengePlayerStatus.PENDING for p in opponents):
        return False
    joined = [
        p
        for p in opponents
        if p.status in {ChallengePlayerStatus.ACCEPTED, ChallengePlayerStatus.COMPLETED}
    ]
    if not joined:
        return False
    return all(p.status == ChallengePlayerStatus.COMPLETED for p in joined)


def _settle(db: Session, challenge: Challenge) -> Challenge:
    if challenge.status in _TERMINAL:
        loaded = get_challenge(db, challenge.id)
        assert loaded is not None
        return loaded

    now = _now()
    past_deadline = _deadline_passed(challenge, now)
    opponents = [p for p in challenge.players if p.role == ChallengePlayerRole.OPPONENT]
    joined = [
        p
        for p in opponents
        if p.status in {ChallengePlayerStatus.ACCEPTED, ChallengePlayerStatus.COMPLETED}
    ]
    pending = [p for p in opponents if p.status == ChallengePlayerStatus.PENDING]
    declined = [p for p in opponents if p.status == ChallengePlayerStatus.DECLINED]

    if not joined and (past_deadline or (opponents and len(declined) == len(opponents))):
        for p in pending:
            p.status = ChallengePlayerStatus.DECLINED
        challenge.status = ChallengeStatus.EXPIRED
        challenge.settled_at = now
        challenge.updated_at = now
        challenge.result = {
            "kind": "expired",
            "reason": "deadline" if past_deadline else "all_declined",
            "winner_ids": [],
            "payouts": [],
            "finishers": [_public_player(p) for p in challenge.players if p.role == ChallengePlayerRole.HOST],
            "forfeiters": [],
        }
        db.commit()
        loaded = get_challenge(db, challenge.id)
        assert loaded is not None
        return loaded

    if not past_deadline and not _ready_to_complete(challenge):
        db.commit()
        loaded = get_challenge(db, challenge.id)
        assert loaded is not None
        return loaded

    forfeiters: list[ChallengePlayer] = []
    if past_deadline:
        for p in joined:
            if p.status == ChallengePlayerStatus.ACCEPTED:
                p.status = ChallengePlayerStatus.FORFEITED
                forfeiters.append(p)
        for p in pending:
            p.status = ChallengePlayerStatus.DECLINED

    finishers = [
        p
        for p in challenge.players
        if p.status == ChallengePlayerStatus.COMPLETED
    ]
    if not finishers:
        host = next(p for p in challenge.players if p.role == ChallengePlayerRole.HOST)
        finishers = [host]

    payouts = _distribute_pot(db, challenge, finishers)
    kind = "forfeited" if forfeiters else "completed"
    challenge.status = ChallengeStatus.FORFEITED if forfeiters and not any(
        p.role == ChallengePlayerRole.OPPONENT and p.status == ChallengePlayerStatus.COMPLETED
        for p in challenge.players
    ) else ChallengeStatus.COMPLETED
    if forfeiters and challenge.status != ChallengeStatus.COMPLETED:
        kind = "forfeited"
    challenge.settled_at = now
    challenge.updated_at = now
    challenge.result = {
        "kind": kind,
        "winner_ids": [p["user_id"] for p in payouts if p["amount"] > 0],
        "payouts": payouts,
        "finishers": [_public_player(p) for p in finishers],
        "forfeiters": [_public_player(p) for p in forfeiters],
    }
    _record_challenge_wins(db, challenge, payouts)
    db.commit()
    loaded = get_challenge(db, challenge.id)
    assert loaded is not None
    return loaded


def _record_challenge_wins(db: Session, challenge: Challenge, payouts: list[dict]) -> None:
    for payout in payouts:
        if int(payout.get("amount") or 0) <= 0:
            continue
        winner = get_user_by_id(db, int(payout["user_id"]))
        if winner is None:
            continue
        record_activity(
            db,
            winner,
            ActivityKind.WON_CHALLENGE,
            course_name=challenge.course_name,
            payload={"challenge_id": challenge.id, "amount": payout["amount"]},
            commit=False,
        )


def _public_player(player: ChallengePlayer) -> dict:
    username = player.user.username if player.user is not None else ""
    return {
        "user_id": player.user_id,
        "username": username,
        "total": player.total,
        "status": player.status.value,
    }


def _distribute_pot(
    db: Session,
    challenge: Challenge,
    finishers: list[ChallengePlayer],
) -> list[dict]:
    pot = int(challenge.pot_amount)
    if pot <= 0 or not finishers:
        return []

    scored = [p for p in finishers if p.total is not None]
    pool = scored or finishers
    best = min(int(p.total) if p.total is not None else 10**9 for p in pool)
    winners = [p for p in pool if (p.total is not None and int(p.total) == best)] or pool
    winners = sorted(winners, key=lambda p: p.user_id)

    share = pot // len(winners)
    remainder = pot % len(winners)
    payouts: list[dict] = []
    remaining = pot
    for i, player in enumerate(winners):
        amount = share + (1 if i < remainder else 0)
        if amount <= 0:
            continue
        user = player.user or get_user_by_id(db, player.user_id)
        if user is None:
            continue
        credit_tokens(
            db,
            user,
            amount=amount,
            source=TokenSource.CHALLENGE_WIN,
            reason=f"Challenge #{challenge.id} payout",
            reference=_ref(challenge.id),
            commit=False,
        )
        remaining -= amount
        challenge.pot_amount = remaining
        if player.user is not None:
            player.user.token_balance = user.token_balance
        payouts.append(
            {
                "user_id": player.user_id,
                "username": user.username,
                "amount": amount,
                "total": player.total,
            }
        )
    challenge.pot_amount = 0
    return payouts
