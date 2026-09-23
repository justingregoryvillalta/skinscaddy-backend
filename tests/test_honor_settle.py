from __future__ import annotations

import inspect
import urllib.request
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.models.round import Round
from app.models.user import User
from app.models.wallet import TokenLedger
from app.services.honor_settle import (
    backfill_bay_hill_honor,
    settle_solo_round,
    solo_honor_amount,
)
from tests.helpers import auth, register

BAY_HILL = "Bay Hill Club Lodge Championship Course"
PARS = [4] * 18
# 14 bogeys and 4 pars = 86. Honor is finish 10 + four pars.
BOGEY_CARD = [5] * 14 + [4] * 4


def _post_round(client: TestClient, token: str, *, holes: int, scores: list[int], pars, course: str):
    body = {
        "course_name": course,
        "num_holes": holes,
        "scores": scores,
    }
    if pars is not None:
        body["pars"] = pars
    return client.post("/api/v1/rounds", headers=auth(token), json=body)


def test_eighteen_pays_finish_and_hole_honor_once(client: TestClient, db_session) -> None:
    user = register(client, "solo")
    scores = [4] * 16 + [3, 2]  # 16 pars, birdie, eagle on par 4
    created = _post_round(
        client,
        user["access_token"],
        holes=18,
        scores=scores,
        pars=PARS,
        course="Pebble Beach",
    )
    assert created.status_code == 201, created.text
    # 10 + 16 pars + birdie 2 + eagle 3 = 31
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 100 + 31

    record = db_session.get(Round, created.json()["id"])
    owner = db_session.get(User, user["user"]["id"])
    assert record is not None and owner is not None
    again = settle_solo_round(db_session, owner, record)
    assert again is not None
    db_session.expire_all()
    credits = (
        db_session.query(TokenLedger)
        .filter(TokenLedger.reference == f"honor:round:{record.id}")
        .all()
    )
    assert len(credits) == 1
    assert credits[0].amount == 31
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 131


def test_nine_pays_finish_and_hole_honor_once(client: TestClient, db_session) -> None:
    user = register(client, "nine")
    # 7 pars, birdie, eagle on par 4. Finish 5 + 7 + 2 + 3 = 17.
    created = _post_round(
        client,
        user["access_token"],
        holes=9,
        scores=[4] * 7 + [3, 2],
        pars=[4] * 9,
        course="Local Nine",
    )
    assert created.status_code == 201, created.text
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 117

    record = db_session.get(Round, created.json()["id"])
    owner = db_session.get(User, user["user"]["id"])
    assert record is not None and owner is not None
    again = settle_solo_round(db_session, owner, record)
    assert again is not None
    db_session.expire_all()
    credits = (
        db_session.query(TokenLedger)
        .filter(TokenLedger.reference == f"honor:round:{record.id}")
        .all()
    )
    assert len(credits) == 1
    assert credits[0].amount == 17
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 117


def test_abandoned_mid_round_pays_nothing(client: TestClient, db_session) -> None:
    user = register(client, "walkoff")
    owner = db_session.get(User, user["user"]["id"])
    assert owner is not None
    abandoned = Round(
        user_id=owner.id,
        course_name="Walk-off",
        course_id=None,
        num_holes=18,
        scores=[4] * 9,
        pars=[4] * 9,
        total=36,
        created_at=datetime(2026, 9, 19, 15, tzinfo=timezone.utc),
    )
    db_session.add(abandoned)
    db_session.commit()
    db_session.refresh(abandoned)
    assert solo_honor_amount(abandoned) == 0
    assert settle_solo_round(db_session, owner, abandoned) is None
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 100


def test_bogey_or_worse_is_finish_only(client: TestClient) -> None:
    user = register(client, "bogeys")
    scores = [5] * 18
    created = _post_round(
        client,
        user["access_token"],
        holes=18,
        scores=scores,
        pars=PARS,
        course="Bogey Loop",
    )
    assert created.status_code == 201, created.text
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 110


def test_reads_do_not_settle_or_call_out(client: TestClient, monkeypatch) -> None:
    user = register(client, "reader")
    created = _post_round(
        client,
        user["access_token"],
        holes=18,
        scores=[4] * 18,
        pars=PARS,
        course="Already Paid",
    )
    assert created.status_code == 201, created.text
    before = client.get("/api/v1/wallet", headers=auth(user["access_token"])).json()["balance"]

    def blocked(*_args, **_kwargs):
        raise AssertionError("outbound http")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)

    import app.api.v1.rounds as rounds_api
    import app.api.v1.wallet as wallet_api
    import app.services.rounds as rounds_svc

    blob = " ".join(
        (
            inspect.getsource(rounds_api.get_rounds),
            inspect.getsource(wallet_api.read_wallet),
            inspect.getsource(rounds_svc.list_rounds),
            inspect.getsource(rounds_svc.get_round),
        )
    ).lower()
    assert "opengolf" not in blob
    assert "open_golf" not in blob

    listed = client.get("/api/v1/rounds", headers=auth(user["access_token"]))
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert listed.status_code == 200
    assert wallet.status_code == 200
    assert wallet.json()["balance"] == before
    assert len(listed.json()["rounds"]) == 1


def test_backfill_only_bay_hill_sep19_for_justinv(client: TestClient, db_session) -> None:
    owner = register(client, "justinv")
    other = register(client, "bob")
    when = datetime(2026, 9, 19, 18, tzinfo=timezone.utc)

    def add_card(user_id: int, course: str, when_at: datetime, holes: int = 18) -> Round:
        scores = BOGEY_CARD if holes == 18 else [4] * holes
        pars = PARS if holes == 18 else [4] * holes
        row = Round(
            user_id=user_id,
            course_name=course,
            course_id=None,
            num_holes=holes,
            scores=scores,
            pars=pars,
            total=sum(scores),
            created_at=when_at,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    bay = add_card(owner["user"]["id"], BAY_HILL, when)
    other_course = add_card(owner["user"]["id"], "Streamsong", when)
    other_day = add_card(
        owner["user"]["id"],
        BAY_HILL,
        datetime(2026, 9, 20, 18, tzinfo=timezone.utc),
    )
    short = add_card(owner["user"]["id"], BAY_HILL, when, holes=9)
    bob_card = add_card(other["user"]["id"], BAY_HILL, when)

    assert backfill_bay_hill_honor(db_session) == 1
    assert backfill_bay_hill_honor(db_session) == 0

    def paid(round_id: int) -> int:
        return (
            db_session.query(TokenLedger)
            .filter(TokenLedger.reference == f"honor:round:{round_id}")
            .count()
        )

    assert paid(bay.id) == 1
    assert paid(other_course.id) == 0
    assert paid(other_day.id) == 0
    assert paid(short.id) == 0
    assert paid(bob_card.id) == 0

    wallet = client.get("/api/v1/wallet", headers=auth(owner["access_token"]))
    # welcome 100 + finish 10 + four pars
    assert wallet.json()["balance"] == 114
    bob_wallet = client.get("/api/v1/wallet", headers=auth(other["access_token"]))
    assert bob_wallet.json()["balance"] == 100
