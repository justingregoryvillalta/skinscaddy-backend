from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.scramble import (
    ScrambleHoleScore,
    ScrambleMember,
    ScrambleRound,
    ScrambleStatus,
    ScrambleTeam,
)
from app.models.user import User
from app.schemas.scramble import CreateTeamInput

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ScrambleError(ValueError):
    pass


class ScrambleNotFoundError(ScrambleError):
    pass


class ScrambleForbiddenError(ScrambleError):
    pass


class ScrambleStateError(ScrambleError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_code(raw: str) -> str:
    text = raw or ""
    low = text.lower()
    if "code=" in low:
        text = text.split("code=", 1)[-1]
        text = text.split("&", 1)[0].split("#", 1)[0]
    return "".join(ch for ch in text.upper() if ch.isalnum())


def deep_link(code: str) -> str:
    return f"skinscaddy://join?code={code}"


def spread_start_holes(n_teams: int, n_holes: int) -> list[int]:
    step = max(1, n_holes // max(1, n_teams))
    return [((i * step) % n_holes) + 1 for i in range(n_teams)]


def team_current_hole(start_hole: int, holes_played: int, num_holes: int) -> int:
    if holes_played >= num_holes:
        return 0
    return ((int(start_hole) - 1 + int(holes_played)) % num_holes) + 1


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def _load_options():
    return (
        selectinload(ScrambleRound.host),
        selectinload(ScrambleRound.teams)
        .selectinload(ScrambleTeam.members)
        .selectinload(ScrambleMember.user),
        selectinload(ScrambleRound.members).selectinload(ScrambleMember.user),
        selectinload(ScrambleRound.members).selectinload(ScrambleMember.team),
        selectinload(ScrambleRound.scores),
    )


def get_scramble(db: Session, scramble_id: int) -> ScrambleRound | None:
    return db.scalar(
        select(ScrambleRound)
        .options(*_load_options())
        .where(ScrambleRound.id == scramble_id)
        .execution_options(populate_existing=True)
    )


def get_scramble_by_code(db: Session, code: str) -> ScrambleRound | None:
    key = normalize_code(code)
    if len(key) < 4:
        return None
    return db.scalar(
        select(ScrambleRound)
        .options(*_load_options())
        .where(ScrambleRound.join_code == key)
        .execution_options(populate_existing=True)
    )


def _require(db: Session, scramble_id: int) -> ScrambleRound:
    scramble = get_scramble(db, scramble_id)
    if scramble is None:
        raise ScrambleNotFoundError("Scramble not found.")
    return scramble


def _member_for(scramble: ScrambleRound, user_id: int) -> ScrambleMember | None:
    return next((m for m in scramble.members if m.user_id == user_id), None)


def _require_member(scramble: ScrambleRound, actor: User) -> ScrambleMember:
    member = _member_for(scramble, actor.id)
    if member is None:
        raise ScrambleForbiddenError("You are not in this scramble.")
    return member


def _scores_by_team_hole(scramble: ScrambleRound) -> dict[tuple[int, int], ScrambleHoleScore]:
    return {(row.team_id, row.hole): row for row in scramble.scores}


def _holes_played(scramble: ScrambleRound, team: ScrambleTeam) -> int:
    return sum(1 for row in scramble.scores if row.team_id == team.id)


def _hole_complete(scramble: ScrambleRound, hole: int) -> bool:
    team_ids = {team.id for team in scramble.teams}
    posted = {row.team_id for row in scramble.scores if row.hole == hole}
    return team_ids <= posted


def _settled_holes(scramble: ScrambleRound) -> set[int]:
    return {int(item["hole"]) for item in (scramble.skin_results or []) if "hole" in item}


def _bump(scramble: ScrambleRound) -> None:
    scramble.revision = int(scramble.revision or 0) + 1
    scramble.updated_at = _now()


def create_scramble(
    db: Session,
    actor: User,
    *,
    course_name: str,
    course_id: str | None,
    num_holes: int,
    wager_amount: int,
    teams: list[CreateTeamInput],
    host_team_index: int,
    pars: list[int] | None,
) -> ScrambleRound:
    n_holes = 18 if num_holes >= 18 else 9
    n_teams = len(teams)
    if n_teams < 2 or n_teams > 6:
        raise ScrambleStateError("A scramble needs 2 to 6 teams.")
    defaults = spread_start_holes(n_teams, n_holes)
    starts = []
    for i, team in enumerate(teams):
        start = team.start_hole if team.start_hole is not None else defaults[i]
        starts.append(max(1, min(n_holes, int(start))))

    code = _new_code()
    for _ in range(16):
        exists = db.scalar(select(ScrambleRound.id).where(ScrambleRound.join_code == code))
        if exists is None:
            break
        code = _new_code()
    else:
        raise ScrambleStateError("Could not allocate a join code.")

    unit = max(1, int(wager_amount))
    scramble = ScrambleRound(
        join_code=code,
        host_id=actor.id,
        course_name=course_name,
        course_id=course_id,
        num_holes=n_holes,
        wager_amount=unit,
        status=ScrambleStatus.ACTIVE,
        pars=list(pars) if pars else None,
        skin_unit=unit,
        skin_pot=unit,
        skin_stack=1,
        skin_results=[],
        revision=1,
    )
    db.add(scramble)
    db.flush()
    created_teams: list[ScrambleTeam] = []
    for i, spec in enumerate(teams):
        row = ScrambleTeam(
            scramble_id=scramble.id,
            index=i,
            name=spec.name,
            start_hole=starts[i],
            skins_won=0,
        )
        db.add(row)
        created_teams.append(row)
    db.flush()
    host_team = created_teams[host_team_index]
    db.add(
        ScrambleMember(
            scramble_id=scramble.id,
            team_id=host_team.id,
            user_id=actor.id,
        )
    )
    db.commit()
    loaded = get_scramble(db, scramble.id)
    assert loaded is not None
    return loaded


def join_scramble(db: Session, actor: User, code: str, team_index: int) -> ScrambleRound:
    scramble = get_scramble_by_code(db, code)
    if scramble is None:
        raise ScrambleNotFoundError("No scramble found for that join code.")
    if scramble.status != ScrambleStatus.ACTIVE:
        raise ScrambleStateError("This scramble is no longer open.")
    teams = sorted(scramble.teams, key=lambda t: t.index)
    if team_index < 0 or team_index >= len(teams):
        raise ScrambleStateError("team_index is out of range.")
    target = teams[team_index]
    existing = _member_for(scramble, actor.id)
    if existing is not None:
        if existing.team_id == target.id:
            return scramble
        posted = any(row.posted_by_id == actor.id for row in scramble.scores)
        if posted:
            raise ScrambleStateError("You cannot switch teams after posting a score.")
        existing.team_id = target.id
    else:
        db.add(
            ScrambleMember(
                scramble_id=scramble.id,
                team_id=target.id,
                user_id=actor.id,
            )
        )
    _bump(scramble)
    db.commit()
    loaded = get_scramble(db, scramble.id)
    assert loaded is not None
    return loaded


def post_score(
    db: Session,
    actor: User,
    scramble_id: int,
    *,
    strokes: int,
    hole: int | None = None,
) -> ScrambleRound:
    scramble = db.scalar(
        select(ScrambleRound)
        .options(*_load_options())
        .where(ScrambleRound.id == scramble_id)
        .with_for_update()
    )
    if scramble is None:
        raise ScrambleNotFoundError("Scramble not found.")
    if scramble.status != ScrambleStatus.ACTIVE:
        raise ScrambleStateError("This scramble is complete.")
    member = _require_member(scramble, actor)
    team = next(t for t in scramble.teams if t.id == member.team_id)
    played = _holes_played(scramble, team)
    current = team_current_hole(team.start_hole, played, scramble.num_holes)
    if current == 0:
        raise ScrambleStateError("Your team has already finished the card.")
    target = int(hole) if hole is not None else current
    if target != current:
        raise ScrambleStateError(f"Your team is on hole {current}.")
    if any(row.team_id == team.id and row.hole == target for row in scramble.scores):
        raise ScrambleStateError("That hole is already posted for your team.")

    score = ScrambleHoleScore(
        scramble_id=scramble.id,
        team_id=team.id,
        hole=target,
        strokes=int(strokes),
        posted_by_id=actor.id,
    )
    db.add(score)
    db.flush()
    if score not in scramble.scores:
        scramble.scores.append(score)
    _try_settle_skins(scramble)
    if _all_done(scramble):
        scramble.status = ScrambleStatus.COMPLETED
    _bump(scramble)
    db.commit()
    loaded = get_scramble(db, scramble.id)
    assert loaded is not None
    return loaded


def list_my_scrambles_statement(user_id: int):
    """Membership subquery — never SELECT DISTINCT on JSON columns (Postgres 500)."""
    member_ids = select(ScrambleMember.scramble_id).where(ScrambleMember.user_id == user_id)
    return (
        select(ScrambleRound)
        .options(*_load_options())
        .where(ScrambleRound.id.in_(member_ids))
        .order_by(ScrambleRound.updated_at.desc(), ScrambleRound.id.desc())
    )


def list_my_scrambles(db: Session, actor: User) -> list[ScrambleRound]:
    return list(db.scalars(list_my_scrambles_statement(actor.id)).all())


def get_visible_scramble(db: Session, actor: User, scramble_id: int) -> ScrambleRound:
    scramble = _require(db, scramble_id)
    _require_member(scramble, actor)
    return scramble


def _all_done(scramble: ScrambleRound) -> bool:
    for team in scramble.teams:
        if _holes_played(scramble, team) < scramble.num_holes:
            return False
    return True


def _try_settle_skins(scramble: ScrambleRound) -> None:
    settled = _settled_holes(scramble)
    results = list(scramble.skin_results or [])
    while True:
        target = None
        for hole in range(1, scramble.num_holes + 1):
            if hole in settled:
                continue
            if not _hole_complete(scramble, hole):
                break
            if hole == 1 or all(h in settled for h in range(1, hole)):
                target = hole
            break
        if target is None:
            break
        teams = sorted(scramble.teams, key=lambda t: t.index)
        lookup = _scores_by_team_hole(scramble)
        scores = [int(lookup[(team.id, target)].strokes) for team in teams]
        best = min(scores)
        winners = [i for i, value in enumerate(scores) if value == best]
        pot = max(int(scramble.skin_unit), int(scramble.skin_pot))
        if len(winners) == 1:
            wi = winners[0]
            teams[wi].skins_won = int(teams[wi].skins_won) + pot
            results.append(
                {
                    "hole": target,
                    "scores": scores,
                    "winner_index": wi,
                    "winner_name": teams[wi].name,
                    "amount": pot,
                    "carry": False,
                    "stack": int(scramble.skin_stack),
                }
            )
            scramble.skin_pot = int(scramble.skin_unit)
            scramble.skin_stack = 1
        else:
            next_pot = pot + int(scramble.skin_unit)
            next_stack = int(scramble.skin_stack) + 1
            results.append(
                {
                    "hole": target,
                    "scores": scores,
                    "winner_index": None,
                    "winner_name": None,
                    "amount": pot,
                    "carry": True,
                    "next_amount": next_pot,
                    "stack": int(scramble.skin_stack),
                    "next_stack": next_stack,
                    "tied": [teams[i].name for i in winners],
                }
            )
            scramble.skin_pot = next_pot
            scramble.skin_stack = next_stack
        settled.add(target)
    scramble.skin_results = results
    flag_modified(scramble, "skin_results")


def viewer_state(scramble: ScrambleRound, actor: User | None) -> dict:
    teams = sorted(scramble.teams, key=lambda t: t.index)
    lookup = _scores_by_team_hole(scramble)
    member = _member_for(scramble, actor.id) if actor is not None else None
    my_team_id = member.team_id if member is not None else None
    settled = _settled_holes(scramble)
    revealed = {hole: _hole_complete(scramble, hole) for hole in range(1, scramble.num_holes + 1)}

    hole_views: list[dict] = []
    for hole in range(1, scramble.num_holes + 1):
        is_open = revealed[hole]
        posted = [lookup.get((team.id, hole)) is not None for team in teams]
        scores: list[int | None] = []
        for team in teams:
            row = lookup.get((team.id, hole))
            if row is None:
                scores.append(None)
            elif is_open or team.id == my_team_id:
                scores.append(int(row.strokes))
            else:
                scores.append(None)
        hole_views.append(
            {
                "hole": hole,
                "revealed": is_open,
                "settled": hole in settled,
                "posted": posted,
                "scores": scores,
            }
        )

    team_views: list[dict] = []
    for team in teams:
        played = _holes_played(scramble, team)
        current = team_current_hole(team.start_hole, played, scramble.num_holes)
        show_all = my_team_id == team.id
        card: list[int | None] = []
        for hole in range(1, scramble.num_holes + 1):
            row = lookup.get((team.id, hole))
            if row is None:
                card.append(None)
            elif show_all or revealed[hole]:
                card.append(int(row.strokes))
            else:
                card.append(None)
        members = [
            m.user
            for m in sorted(team.members, key=lambda item: item.joined_at)
            if m.user is not None
        ]
        team_views.append(
            {
                "index": team.index,
                "name": team.name,
                "start_hole": team.start_hole,
                "current_hole": current,
                "holes_played": played,
                "skins_won": int(team.skins_won),
                "finished": played >= scramble.num_holes,
                "members": members,
                "scores": card,
            }
        )

    my_index = None
    if member is not None:
        team = next((t for t in teams if t.id == member.team_id), None)
        if team is not None:
            my_index = team.index

    return {
        "id": scramble.id,
        "join_code": scramble.join_code,
        "deep_link": deep_link(scramble.join_code),
        "status": scramble.status,
        "course_name": scramble.course_name,
        "course_id": scramble.course_id,
        "num_holes": scramble.num_holes,
        "wager_amount": scramble.wager_amount,
        "skin_unit": scramble.skin_unit,
        "skin_pot": scramble.skin_pot,
        "skin_stack": scramble.skin_stack,
        "revision": scramble.revision,
        "host": scramble.host,
        "my_team_index": my_index,
        "teams": team_views,
        "holes": hole_views,
        "skin_results": list(scramble.skin_results or []),
        "created_at": scramble.created_at,
        "updated_at": scramble.updated_at,
    }


def preview_state(scramble: ScrambleRound) -> dict:
    teams = sorted(scramble.teams, key=lambda t: t.index)
    return {
        "join_code": scramble.join_code,
        "deep_link": deep_link(scramble.join_code),
        "status": scramble.status,
        "course_name": scramble.course_name,
        "num_holes": scramble.num_holes,
        "wager_amount": scramble.wager_amount,
        "teams": [
            {
                "index": team.index,
                "name": team.name,
                "start_hole": team.start_hole,
                "member_count": len(team.members),
            }
            for team in teams
        ],
    }
