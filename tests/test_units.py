"""Small pieces: parsing model replies, the device check, the map schema, the index."""
from __future__ import annotations

import time
from datetime import date

import device, interests
from phases.common import parse_lines
from sources.arxiv import month_ranges
from sources.base import canonical_id, merge
from store import Store
from web import envfile


def test_parse_lines_tolerates_decoration():
    reply = "Sure:\n- i1 keep rl\n**i2** | 7.5 | new | ok | really\ni2 drop\n  i10: sentence"
    got = parse_lines(reply)
    assert got["i1"] == "keep rl"
    assert got["i2"].startswith("| 7.5") or got["i2"].startswith("7.5")
    assert got["i10"] == "sentence"


def test_device_fit():
    s = {"device": {"name": "Mac", "memory_gb": 64}}
    assert "full precision" in device.fit(7e9, s)
    assert "4-bit" in device.fit(70e9, s)
    assert device.fit(400e9, s).startswith("too large")
    assert device.fit(None, s) == "" and device.fit(7e9, {"device": {}}) == ""


def test_map_validation():
    ok = {"about": "x", "topics": [{"id": "rl", "name": "RL", "summary": "s",
                                    "queries": {"arxiv": ["grpo"]}}]}
    assert interests.validate(ok) == []
    bad = {"topics": [{"id": "rl", "name": "RL", "summary": "s", "queries": {"twitter": ["x"]}},
                      {"id": "rl", "name": "x", "summary": "s", "priority": "urgent"}]}
    errors = " ".join(interests.validate(bad))
    assert "unknown query source" in errors and "used twice" in errors and "priority" in errors


def test_month_ranges_cover_the_period():
    ranges = month_ranges(3, today=date(2026, 9, 29))
    assert ranges == [(date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 31)),
                      (date(2026, 9, 1), date(2026, 9, 29))]


def test_canonical_ids_and_merge():
    assert canonical_id("https://arxiv.org/pdf/2609.12345v2") == "arxiv:2609.12345"
    assert canonical_id("https://github.com/Owner/Repo/tree/main", "repo") == "gh:owner/repo"
    assert canonical_id("https://x.com/a?utm_source=y") == canonical_id("https://x.com/a/")
    a = {"id": "arxiv:1", "source": "arxiv", "abstract": "short", "signals": {}}
    b = {"id": "arxiv:1", "source": "hf_papers", "abstract": "a longer one", "signals": {"hf_upvotes": 5}}
    [m] = merge([a, b])
    assert m["abstract"] == "a longer one" and m["signals"]["seen_in"] == ["arxiv", "hf_papers"]


def test_index_records_changes(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    item = {"id": "arxiv:1", "kind": "paper", "source": "arxiv", "title": "t", "abstract": "a",
            "published_at": time.time(), "signals": {}}
    assert store.upsert_item(item, "knowledge", backfill=True)
    assert not store.upsert_item(dict(item, source="hf_papers"), "knowledge")
    row = store.item("arxiv:1")
    assert row["change_note"] == "now on hf_papers"
    store.upsert_item(dict(item, signals={"code_url": "https://github.com/a/b"}), "knowledge")
    assert "code released" in store.item("arxiv:1")["change_note"]


def test_envfile_keeps_other_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nA_KEY=1\n")
    envfile.set_key(path, "B_KEY", "2")
    envfile.set_key(path, "A_KEY", "3")
    assert path.read_text() == "# comment\nA_KEY=3\nB_KEY=2\n"
    assert envfile.mask("abcdefghijkl") == "••••ijkl"


def test_merge_keeps_mine_and_adds_theirs():
    mine = {"about": "me", "topics": [
        {"id": "rl", "name": "RL", "summary": "mine", "priority": "core",
         "queries": {"arxiv": ["grpo"]}, "watch": {"repos": ["a/b"]}}]}
    theirs = {"about": "them", "topics": [
        {"id": "rl", "name": "Renamed", "summary": "theirs", "priority": "side",
         "queries": {"arxiv": ["GRPO", "rlvr"], "web": ["x"]}, "include": ["y"]},
        {"id": "new", "name": "New", "summary": "s"}],
        "exclude_everywhere": ["safety"]}
    out = interests.merge(mine, theirs)
    rl = out["topics"][0]
    assert (rl["name"], rl["summary"], rl["priority"]) == ("RL", "mine", "core")
    assert rl["queries"] == {"arxiv": ["grpo", "rlvr"], "web": ["x"]}
    assert rl["include"] == ["y"] and rl["watch"] == {"repos": ["a/b"]}
    assert [t["id"] for t in out["topics"]] == ["rl", "new"]
    assert out["about"] == "me" and out["exclude_everywhere"] == ["safety"]
    assert interests.validate(out) == []
    assert mine["topics"][0]["queries"] == {"arxiv": ["grpo"]}  # input untouched


def test_extract_answer_takes_the_yaml_block():
    text = "Sure!\n```yaml\nabout: x\ntopics: []\n```\nChanges:\n- none"
    assert interests.extract_answer(text) == "about: x\ntopics: []\n"
    assert interests.extract_answer("about: y\n") == "about: y\n"
