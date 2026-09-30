"""The whole run, offline: collect → triage → score → select → read → news → write."""
from __future__ import annotations

import json
import time

import pytest

from config import Config
from pipeline import RunOptions, run_digest
from runlog import RunContext
from store import Store

from conftest import DAY, news_item, paper


def run(cfg: Config, **opts) -> int:
    with RunContext(cfg, "run") as rc:
        return run_digest(cfg, rc, RunOptions(**opts))


def test_first_run_writes_a_digest(world, cfg, home):
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 21)]
    run_id = run(cfg)

    store = Store(cfg.db_path)
    row = store.run(run_id)
    assert row["status"] == "done"
    d = json.loads(row["digest"])
    assert d["counts"]["notes"] == 8  # max_notes_per_topic 4 × the 2 topics the fake uses
    assert d["counts"]["mentions"] == 12
    assert "[1]" in d["article"] and "[99]" not in d["article"]
    notes = [r for s in d["sections"] for r in s["notes"]]
    assert [r["n"] for r in notes] == list(range(1, 9))
    assert all("**Open it if**" in r["note"] for r in notes)
    # saved in both places, citations turned into links
    vault = list((home / "vault").glob("*.md"))
    assert len(vault) == 1 and "## This time" in vault[0].read_text()
    assert "[1](https://arxiv.org/abs/" in vault[0].read_text()
    assert list(cfg.digests_dir.glob("*.md"))
    assert store.get("knowledge_until") == pytest.approx(row["k_until"])
    assert store.get("news_until") is None  # news off by default


def test_nothing_is_shown_twice_without_a_reason(world, cfg):
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 6)]
    first = run(cfg)
    world.calls.clear()
    second = run(cfg, days=3)

    store = Store(cfg.db_path)
    d = json.loads(store.run(second)["digest"])
    assert d["counts"]["notes"] == 0 and d["counts"]["mentions"] == 0
    # the index saved the work: no triage or scoring of known items again
    assert "triage" not in world.calls and "score" not in world.calls
    assert first != second


def test_a_changed_item_comes_back_with_its_reason(world, cfg):
    world.knowledge = [paper(1, "Weight transfer for RL")]
    run(cfg)
    world.knowledge = [paper(1, "Weight transfer for RL", code_url="https://github.com/a/b")]
    second = run(cfg, days=3)
    d = json.loads(Store(cfg.db_path).run(second)["digest"])
    notes = [r for s in d["sections"] for r in s["notes"]]
    assert len(notes) == 1 and notes[0]["back"] == "code released"


def test_a_new_topic_brings_old_items_back(world, cfg, home, monkeypatch):
    import conftest
    import yaml

    world.knowledge = [paper(1, "Weight transfer for RL")]
    run(cfg)
    path = home / "config/interests.yaml"
    data = yaml.safe_load(path.read_text())
    data["topics"].append({"id": "fresh-topic", "name": "Fresh topic", "priority": "core",
                           "summary": "Something the map did not have before."})
    path.write_text(yaml.safe_dump(data))
    orig = conftest.fake_reply
    monkeypatch.setattr(conftest, "fake_reply", lambda w, phase, user: (
        orig(w, phase, user).replace("post-training-rl", "fresh-topic")
        if phase == "triage" else orig(w, phase, user)))
    second = run(Config(), days=3)
    d = json.loads(Store(cfg.db_path).run(second)["digest"])
    backs = [r["back"] for s in d["sections"] for r in s["notes"]] + \
            [r["back"] for r in d["mentions"]]
    assert backs == ["matches your new topic \u201cFresh topic\u201d"]


def test_a_failed_run_resumes_without_redoing_work(world, cfg):
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 11)]
    world.fail_phase = "note"
    with pytest.raises(Exception):
        run(cfg)
    store = Store(cfg.db_path)
    failed = store.runs("run", limit=1)[0]
    assert store.get("knowledge_until") is None  # the window did not move
    triage_calls = world.calls["triage"]

    world.fail_phase = None
    resumed = run(cfg, resume=failed["id"])
    assert resumed == failed["id"]
    assert world.calls["triage"] == triage_calls
    assert Store(cfg.db_path).run(resumed)["status"] == "done"


def test_news_window_never_overlaps_and_is_capped(world, cfg, home):
    world.knowledge = [paper(1, "A paper")]
    world.news = [news_item(1, "Model X released", days_ago=2),
                  news_item(2, "Old story", days_ago=20)]
    first = run(cfg, news=True)
    since, until = world.windows["news"][0]
    assert until - since == pytest.approx(7 * DAY, abs=60)  # capped at max_days
    d = json.loads(Store(cfg.db_path).run(first)["digest"])
    assert "Model X released" not in d["news"]["markdown"]  # the model wrote its own words
    assert "(https://example.com/news/1)" in d["news"]["markdown"]

    run(cfg, news=True)
    since2, _ = world.windows["news"][1]
    assert since2 == pytest.approx(until, abs=1)  # starts where the last one ended


def test_missing_api_key_fails_before_collecting(world, cfg, home):
    (home / ".env").write_text("")
    with pytest.raises(Exception, match="DEEPSEEK_API_KEY"):
        run(Config())
    assert world.windows["knowledge"] == []


def test_explore_share_keeps_room_outside_the_map(world, cfg, monkeypatch):
    import conftest
    orig = conftest.fake_reply

    def with_other(world_, phase, user):
        out = orig(world_, phase, user)
        if phase == "triage":
            lines = out.splitlines()
            lines[-1] = lines[-1].rsplit(" ", 1)[0] + " other"
            return "\n".join(lines)
        return out

    monkeypatch.setattr(conftest, "fake_reply", with_other)
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 21)]
    run_id = run(cfg)
    d = json.loads(Store(cfg.db_path).run(run_id)["digest"])
    assert "other" in [s["topic"] for s in d["sections"]]


def test_stale_running_rows_are_abandoned(world, cfg):
    store = Store(cfg.db_path)
    stale = store.create_run("run", "data/logs/x", k_since=time.time() - DAY, k_until=time.time())
    world.knowledge = [paper(1, "A paper")]
    run(cfg)
    assert Store(cfg.db_path).run(stale)["status"] == "abandoned"


def test_items_over_the_triage_limit_carry_over(world, cfg, home):
    import yaml

    from phases.common import Job
    path = home / "config/settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["run"]["triage_max"] = 5
    path.write_text(yaml.safe_dump(data))
    cfg = Config()
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 13)]
    run(cfg)
    world.knowledge = []          # nothing new: only the carried-over items remain
    run(cfg, days=1)
    run(cfg, days=1)
    store = Store(cfg.db_path)
    with RunContext(cfg, "check", take_lock=False) as rc:
        key = Job(cfg, store, rc).key("triage")
    ids = [p["id"] for p in [paper(n, "") for n in range(1, 13)]]
    assert len(store.triaged(key, ids)) == 12          # 5 + 5 + 2 over three runs
    assert store.untriaged_recent(key, 0, set()) == []
