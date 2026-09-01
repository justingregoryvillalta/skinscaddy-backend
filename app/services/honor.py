"""Honor Board — bag tags, metals, friends board, public hot list.

Metal rules match clubhouse.py: bronze 1, silver 10, gold 25, platinum 50.
Bag Rack uses unique-tag thresholds 5 / 7 / 8 / 9.
Member has no levels. Season Plate is locked and never auto-awarded.
Tokens never buy rank.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, union
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.challenge import Challenge, ChallengePlayer
from app.models.friend import FriendRequest, FriendRequestStatus
from app.models.round import Round
from app.models.user import User
from app.models.wallet import TokenDirection, TokenLedger, TokenSource

TAGS: tuple[dict[str, str], ...] = (
    {
        "id": "member",
        "icon": "bagtag_member.png",
        "name": "Member",
        "mark": "M",
        "what": "You're on the club roll.",
        "how": "Sign in to your account.",
    },
    {
        "id": "first_tee",
        "icon": "bagtag_first_tee.png",
        "name": "First Tee",
        "mark": "1",
        "what": "You've teed it up in SkinsCaddy.",
        "how": "Post a round or a hole score.",
    },
    {
        "id": "skins",
        "icon": "bagtag_skins.png",
        "name": "Skins",
        "mark": "S",
        "what": "You took a skins pot.",
        "how": "Win the low score on a hole in Skins.",
    },
    {
        "id": "birdie",
        "icon": "bagtag_birdie.png",
        "name": "Birdie",
        "mark": "B",
        "what": "You went one under.",
        "how": "Card a birdie on any hole.",
    },
    {
        "id": "nineteenth",
        "icon": "bagtag_nineteenth.png",
        "name": "19th Hole",
        "mark": "19",
        "what": "You played a side game.",
        "how": "Start or settle a side game.",
    },
    {
        "id": "partners",
        "icon": "bagtag_partners.png",
        "name": "Partners",
        "mark": "P",
        "what": "You've got someone in the group.",
        "how": "Add a friend.",
    },
    {
        "id": "regular",
        "icon": "bagtag_regular.png",
        "name": "The Loop",
        "mark": "L",
        "what": "You're on a run. The book keeps filling.",
        "how": "Keep looping — post a round, add a friend, play a side game, card a birdie, or take a skins pot.",
    },
    {
        "id": "rack",
        "icon": "bagtag_rack.png",
        "name": "Bag Rack",
        "mark": "5",
        "what": "The bag is filling up.",
        "how": "Earn unique tags. Five for bronze; more fills the rack.",
    },
    {
        "id": "season_plate",
        "icon": "bagtag_season_plate.png",
        "name": "Season Plate",
        "mark": "SP",
        "what": "Honor for the season champion.",
        "how": "Posted when the season closes.",
    },
)

METALS: tuple[str, ...] = ("bronze", "silver", "gold", "platinum")
METAL_THRESHOLDS: tuple[int, ...] = (1, 10, 25, 50)
RACK_THRESHOLDS: tuple[int, ...] = (5, 7, 8, 9)
_METAL_RANK = {"": 0, "bronze": 1, "silver": 2, "gold": 3, "platinum": 4}

_LOCKED_IDS = frozenset({"season_plate"})
_NO_LEVEL_IDS = frozenset({"member", "season_plate"})
_LEVELING_IDS = frozenset(
    {"first_tee", "skins", "birdie", "nineteenth", "partners", "regular", "rack"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_season(today: date | None = None) -> dict[str, str]:
    d = today or date.today()
    y = d.year
    m = d.month
    if m in (3, 4, 5):
        return {"id": f"spring-{y}", "name": f"Spring {y}", "window": "Mar – May"}
    if m in (6, 7, 8):
        return {"id": f"summer-{y}", "name": f"Summer {y}", "window": "Jun – Aug"}
    if m in (9, 10, 11):
        return {"id": f"fall-{y}", "name": f"Fall {y}", "window": "Sep – Nov"}
    wy = y if m == 12 else y - 1
    return {"id": f"winter-{wy}", "name": f"Winter {wy}", "window": "Dec – Feb"}


def thresholds_for(tag_id: str) -> tuple[int, ...]:
    if tag_id == "rack":
        return RACK_THRESHOLDS
    return METAL_THRESHOLDS


def metal_for(tag_id: str, count: int) -> str:
    if tag_id in _NO_LEVEL_IDS:
        return ""
    metal = ""
    n = int(count or 0)
    for name, need in zip(METALS, thresholds_for(tag_id)):
        if n >= need:
            metal = name
        else:
            break
    return metal


def next_at_for(tag_id: str, count: int) -> int | None:
    if tag_id in _NO_LEVEL_IDS:
        return None
    n = int(count or 0)
    for need in thresholds_for(tag_id):
        if n < need:
            return need
    return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _parse_rec(rec: Any) -> tuple[bool, int, str, str]:
    if not isinstance(rec, dict):
        return False, 0, "", ""
    when = str(rec.get("earned_at") or "")
    count = _as_int(rec.get("count"), 0)
    metal = str(rec.get("metal") or "").strip().lower()
    if metal not in _METAL_RANK:
        metal = ""
    earned = bool(when) or count > 0 or bool(metal)
    if not earned:
        return False, 0, "", ""
    if count < 1:
        count = 1
    return True, count, metal, when


def _is_earned(rec: Any) -> bool:
    return _parse_rec(rec)[0]


def _set_progress(blob: dict[str, Any], tag_id: str, count: int) -> bool:
    if tag_id == "season_plate":
        return False
    tags = blob.setdefault("tags", {})
    prev = tags.get(tag_id)
    prev_earned, prev_count, prev_metal, prev_when = _parse_rec(prev)
    if prev_earned and not prev_metal and tag_id not in _NO_LEVEL_IDS:
        prev_metal = metal_for(tag_id, prev_count)

    new_count = max(_as_int(count, 0), prev_count)

    if tag_id in _NO_LEVEL_IDS:
        if prev_earned:
            return False
        if new_count < 1:
            return False
        tags[tag_id] = {
            "earned_at": _now(),
            "count": max(new_count, 1),
            "metal": "",
        }
        return True

    metal = metal_for(tag_id, new_count)
    if not metal:
        return False

    tags[tag_id] = {
        "earned_at": prev_when or _now(),
        "count": new_count,
        "metal": metal,
    }
    newly = not prev_earned
    upgraded = _METAL_RANK.get(metal, 0) > _METAL_RANK.get(prev_metal, 0)
    return newly or upgraded


def _unique_play_count(blob: dict[str, Any]) -> int:
    tags = blob.get("tags") if isinstance(blob.get("tags"), dict) else {}
    n = 0
    for tid, rec in tags.items():
        if tid == "season_plate":
            continue
        if _is_earned(rec):
            n += 1
    return n


def _mark(blob: dict[str, Any], tag_id: str, count: int) -> None:
    _set_progress(blob, tag_id, count)


def _tags_dict(user: User) -> dict[str, Any]:
    raw = getattr(user, "honor_tags", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    return dict(raw) if isinstance(raw, dict) else {}


def _round_count(db: Session, user_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Round).where(Round.user_id == user_id))
        or 0
    )


def _birdies_from_rounds(db: Session, user_id: int) -> int:
    rows = db.scalars(select(Round).where(Round.user_id == user_id)).all()
    n = 0
    for row in rows:
        scores = list(row.scores or [])
        pars = list(row.pars or [])
        if not pars or len(pars) != len(scores):
            continue
        for score, par in zip(scores, pars):
            try:
                if int(score) == int(par) - 1:
                    n += 1
            except (TypeError, ValueError):
                continue
    return n


def _ledger_credit_count(db: Session, user_id: int, source: TokenSource) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TokenLedger)
            .where(
                TokenLedger.user_id == user_id,
                TokenLedger.direction == TokenDirection.CREDIT,
                TokenLedger.source == source,
            )
        )
        or 0
    )


def _friend_count(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(FriendRequest)
            .where(
                FriendRequest.status == FriendRequestStatus.ACCEPTED,
                or_(
                    FriendRequest.requester_id == user_id,
                    FriendRequest.addressee_id == user_id,
                ),
            )
        )
        or 0
    )


def _challenge_count(db: Session, user_id: int) -> int:
    player_ids = select(ChallengePlayer.challenge_id).where(
        ChallengePlayer.user_id == user_id
    )
    created_ids = select(Challenge.id).where(Challenge.creator_id == user_id)
    sub = union(player_ids, created_ids).subquery()
    return int(db.scalar(select(func.count()).select_from(sub)) or 0)


def _wallet_stats(db: Session, user_id: int) -> tuple[int, int, int]:
    try:
        from app.services.wallet import wallet_totals

        earned, spent = wallet_totals(db, user_id)
    except Exception:
        earned, spent = 0, 0
    ledger = int(
        db.scalar(
            select(func.count())
            .select_from(TokenLedger)
            .where(TokenLedger.user_id == user_id)
        )
        or 0
    )
    return int(earned or 0), int(spent or 0), ledger


def _friend_ids(db: Session, user_id: int) -> list[int]:
    rows = db.scalars(
        select(FriendRequest).where(
            FriendRequest.status == FriendRequestStatus.ACCEPTED,
            or_(
                FriendRequest.requester_id == user_id,
                FriendRequest.addressee_id == user_id,
            ),
        )
    ).all()
    ids: list[int] = []
    for row in rows:
        other = row.addressee_id if row.requester_id == user_id else row.requester_id
        if other not in ids:
            ids.append(other)
    return ids


def _rank_among(users: list[User], user_id: int) -> int | None:
    ordered = sorted(
        users,
        key=lambda u: (-int(getattr(u, "honor_tally", 0) or 0), (u.username or "").lower()),
    )
    for i, row in enumerate(ordered, start=1):
        if row.id == user_id:
            return i
    return None


def rank_friends(db: Session, user: User) -> int | None:
    ids = [user.id] + _friend_ids(db, user.id)
    rows = list(db.scalars(select(User).where(User.id.in_(ids))).all())
    return _rank_among(rows, user.id)


def _earned_count(tags: dict[str, Any]) -> int:
    n = 0
    for rec in tags.values():
        if _is_earned(rec):
            n += 1
    return n


def compact_tags(user: User) -> list[dict[str, Any]]:
    tags = _tags_dict(user)
    out: list[dict[str, Any]] = []
    for spec in TAGS:
        tid = spec["id"]
        got, count, metal, _when = _parse_rec(tags.get(tid))
        if not got:
            continue
        if tid not in _NO_LEVEL_IDS and not metal:
            metal = metal_for(tid, count)
        if tid in _NO_LEVEL_IDS:
            metal = ""
        out.append({"id": tid, "metal": metal, "count": count})
    return out


def regular_metal_for(user: User) -> str:
    tags = _tags_dict(user)
    got, count, metal, _ = _parse_rec(tags.get("regular"))
    tally = int(getattr(user, "honor_tally", 0) or 0)
    if got and not metal:
        metal = metal_for("regular", count)
    if not metal:
        metal = metal_for("regular", tally)
    return metal


def snapshot_user(
    user: User,
    *,
    round_count: int = 0,
    friend_count: int = 0,
    challenge_count: int = 0,
    earned: int = 0,
    spent: int = 0,
    ledger: int = 0,
    rank_friends_n: int | None = None,
) -> dict[str, Any]:
    season = current_season()
    tags_map = _tags_dict(user)
    unique_n = _unique_play_count({"tags": tags_map})
    tally = int(getattr(user, "honor_tally", 0) or 0)
    skins = int(getattr(user, "honor_skins", 0) or 0)
    birdies = int(getattr(user, "honor_birdies", 0) or 0)
    stats = {
        "earned": earned,
        "spent": spent,
        "skins_taken": skins,
        "birdies": birdies,
        "tally": tally,
        "round_count": round_count,
        "friend_count": friend_count,
        "challenge_count": challenge_count,
        "ledger": ledger,
    }

    def _stat_count(tid: str) -> int:
        if tid == "first_tee":
            return round_count
        if tid == "skins":
            return skins
        if tid == "birdie":
            return birdies
        if tid == "nineteenth":
            return challenge_count
        if tid == "partners":
            return friend_count
        if tid == "regular":
            return tally
        if tid == "rack":
            return unique_n
        return 0

    rows = []
    for spec in TAGS:
        tid = spec["id"]
        rec = tags_map.get(tid)
        got, count, metal, when = _parse_rec(rec)
        if got and tid not in _NO_LEVEL_IDS and not metal:
            metal = metal_for(tid, count)
        if got and tid in _NO_LEVEL_IDS:
            metal = ""
        if not got and tid in _LEVELING_IDS:
            count = _stat_count(tid)
            metal = ""
        locked = (not got) and tid in _LOCKED_IDS
        nxt = next_at_for(tid, count) if tid not in _NO_LEVEL_IDS else None
        rows.append(
            {
                "id": tid,
                "name": spec["name"],
                "mark": spec.get("mark") or spec["name"][:1],
                "what": spec.get("what") or "",
                "how": spec["how"],
                "earned": got,
                "locked": locked,
                "earned_at": when,
                "icon": spec.get("icon") or "",
                "metal": metal,
                "count": count,
                "next_at": nxt,
            }
        )
    earned_n = sum(1 for r in rows if r["earned"])
    return {
        "season": season,
        "stats": stats,
        "tags": rows,
        "earned_count": earned_n,
        "tag_total": len(TAGS),
        "tally": tally,
        "rank_friends": rank_friends_n,
    }


def recompute_and_save(
    db: Session,
    user: User,
    *,
    skins_taken: int | None = None,
    birdies: int | None = None,
    round_count: int | None = None,
    friend_count: int | None = None,
    challenge_count: int | None = None,
) -> dict[str, Any]:
    """Recompute tags from tables + stored skins/birdies. Monotonic tally."""
    db_rounds = _round_count(db, user.id)
    db_friends = _friend_count(db, user.id)
    db_challenges = _challenge_count(db, user.id)
    rounds_n = max(db_rounds, _as_int(round_count, 0) if round_count is not None else 0)
    friends_n = max(db_friends, _as_int(friend_count, 0) if friend_count is not None else 0)
    challenges_n = max(
        db_challenges, _as_int(challenge_count, 0) if challenge_count is not None else 0
    )
    db_skins = _ledger_credit_count(db, user.id, TokenSource.SKINS_WIN)
    db_birdies = max(
        _ledger_credit_count(db, user.id, TokenSource.BIRDIE),
        _birdies_from_rounds(db, user.id),
    )
    skins = max(
        int(getattr(user, "honor_skins", 0) or 0),
        db_skins,
        _as_int(skins_taken, 0) if skins_taken is not None else 0,
    )
    birds = max(
        int(getattr(user, "honor_birdies", 0) or 0),
        db_birdies,
        _as_int(birdies, 0) if birdies is not None else 0,
    )

    blob: dict[str, Any] = {"tags": _tags_dict(user), "stats": {}}
    _mark(blob, "member", 1)
    if rounds_n:
        _mark(blob, "first_tee", rounds_n)
    if skins:
        _mark(blob, "skins", skins)
    if birds:
        _mark(blob, "birdie", birds)
    if challenges_n:
        _mark(blob, "nineteenth", challenges_n)
    if friends_n:
        _mark(blob, "partners", friends_n)

    raw_tally = rounds_n + friends_n + challenges_n + skins + birds
    tally = max(int(getattr(user, "honor_tally", 0) or 0), raw_tally)
    if tally:
        _mark(blob, "regular", tally)

    n_unique = _unique_play_count(blob)
    _mark(blob, "rack", n_unique)
    n2 = _unique_play_count(blob)
    if n2 != n_unique:
        _mark(blob, "rack", n2)

    user.honor_tally = tally
    user.honor_skins = skins
    user.honor_birdies = birds
    user.honor_tags = blob["tags"]
    user.honor_updated_at = datetime.now(timezone.utc)
    flag_modified(user, "honor_tags")
    db.add(user)
    db.commit()
    db.refresh(user)

    earned, spent, ledger = _wallet_stats(db, user.id)
    return snapshot_user(
        user,
        round_count=rounds_n,
        friend_count=friends_n,
        challenge_count=challenges_n,
        earned=earned,
        spent=spent,
        ledger=ledger,
        rank_friends_n=rank_friends(db, user),
    )


def _recompute_users(db: Session, users: list[User]) -> list[User]:
    fresh: list[User] = []
    seen: set[int] = set()
    for row in users:
        if row is None or row.id in seen:
            continue
        seen.add(row.id)
        try:
            recompute_and_save(db, row)
            db.refresh(row)
        except Exception:
            pass
        fresh.append(row)
    return fresh


def friends_board(db: Session, actor: User) -> dict[str, Any]:
    ids = [actor.id] + _friend_ids(db, actor.id)
    rows = list(db.scalars(select(User).where(User.id.in_(ids))).all())
    rows = _recompute_users(db, rows)
    ordered = sorted(
        rows,
        key=lambda u: (-int(getattr(u, "honor_tally", 0) or 0), (u.username or "").lower()),
    )
    entries = []
    for i, row in enumerate(ordered, start=1):
        tally = int(getattr(row, "honor_tally", 0) or 0)
        entries.append(
            {
                "rank": i,
                "username": row.username,
                "tally": tally,
                "earned_count": _earned_count(_tags_dict(row)),
                "regular_metal": regular_metal_for(row),
                "tags": compact_tags(row),
            }
        )
    return {"season": current_season(), "entries": entries}


_PLAY_LEDGER_SOURCES = (
    TokenSource.BIRDIE,
    TokenSource.EAGLE,
    TokenSource.SKINS_WIN,
    TokenSource.ROUND_COMPLETE_9,
    TokenSource.ROUND_COMPLETE_18,
    TokenSource.CHALLENGE_WIN,
)


def _stale_play_users(db: Session, *, cap: int = 80) -> list[User]:
    """Players with honor-counting activity whose stored tally is still 0."""
    play = union(
        select(Round.user_id.label("uid")),
        select(FriendRequest.requester_id.label("uid")).where(
            FriendRequest.status == FriendRequestStatus.ACCEPTED
        ),
        select(FriendRequest.addressee_id.label("uid")).where(
            FriendRequest.status == FriendRequestStatus.ACCEPTED
        ),
        select(Challenge.creator_id.label("uid")),
        select(ChallengePlayer.user_id.label("uid")),
        select(TokenLedger.user_id.label("uid")).where(
            TokenLedger.direction == TokenDirection.CREDIT,
            TokenLedger.source.in_(_PLAY_LEDGER_SOURCES),
        ),
    ).subquery()
    filters = [User.id.in_(select(play.c.uid)), User.honor_tally == 0]
    if hasattr(User, "is_disabled"):
        filters.append(User.is_disabled.is_(False))
    return list(db.scalars(select(User).where(*filters).limit(max(1, cap))).all())


def hot_list(db: Session, *, limit: int = 25) -> dict[str, Any]:
    n = max(1, min(int(limit or 25), 50))
    stale = _stale_play_users(db, cap=max(n * 4, 40))
    if stale:
        _recompute_users(db, stale)
    filters = [User.honor_tally > 0]
    if hasattr(User, "is_disabled"):
        filters.append(User.is_disabled.is_(False))
    rows = list(
        db.scalars(
            select(User)
            .where(*filters)
            .order_by(User.honor_tally.desc(), func.lower(User.username).asc())
            .limit(n)
        ).all()
    )
    rows = _recompute_users(db, rows)
    rows = sorted(
        rows,
        key=lambda u: (-int(getattr(u, "honor_tally", 0) or 0), (u.username or "").lower()),
    )
    entries = []
    for i, row in enumerate(rows, start=1):
        tally = int(getattr(row, "honor_tally", 0) or 0)
        entries.append(
            {
                "rank": i,
                "username": row.username,
                "tally": tally,
                "earned_count": _earned_count(_tags_dict(row)),
                "regular_metal": regular_metal_for(row),
            }
        )
    return {"season": current_season(), "entries": entries}
