"""Every page renders, and every write path validates before it writes."""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from config import Config
from pipeline import RunOptions, run_digest
from runlog import RunContext, new_job_dir
from web.app import create_app

from conftest import paper


@pytest.fixture()
def client(home):
    return TestClient(create_app())


@pytest.fixture()
def digest_run(world, home):
    world.knowledge = [paper(n, f"Method number {n}") for n in range(1, 12)]
    cfg = Config()
    with RunContext(cfg, "run") as rc:
        return run_digest(cfg, rc, RunOptions()), rc.name


def test_main_pages_render(client):
    for path in ("/run", "/history", "/settings/interests", "/settings/sources",
                 "/settings/models", "/settings/digest", "/settings/keys"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert 'aria-current="page"' in resp.text
    assert "Make a digest" in client.get("/run").text
    assert "No digests yet" in client.get("/history").text


def test_digest_page_and_job_page(client, digest_run):
    run_id, job = digest_run
    page = client.get(f"/history/{run_id}").text
    assert 'class="cite"' in page and 'id="ref-1"' in page
    assert "Also worth knowing" in page
    assert "Method number" in client.get("/history").text
    md = client.get(f"/history/{run_id}/markdown")
    assert md.status_code == 200 and md.text.startswith("---")
    jp = client.get(f"/jobs/{job}").text
    assert "Model calls" in jp and "triage" in jp
    panel = client.get(f"/jobs/{job}/panel").text
    for text in ("Papers, posts, repos", "Interest map", "Model calls", "Kept, by topic",
                 "Highest so far", "already triaged", "after merging duplicates",
                 "fake_knowledge", "models answering", "notes written", "calls ·"):
        assert text in panel, text
    assert "Not started yet" in panel  # news was off, so that phase never ran
    assert "Deep read" in client.get("/item/arxiv:2609.10001").text


def test_invalid_yaml_is_not_saved(client, home):
    before = (home / "config/interests.yaml").read_text()
    resp = client.post("/settings/file/interests", data={"text": "topics: [1, 2"})
    assert "Not saved" in resp.text
    resp = client.post("/settings/file/interests",
                       data={"text": "about: x\ntopics:\n  - id: Bad Id\n    name: x\n"})
    assert "lowercase slug" in resp.text and "needs a summary" in resp.text
    assert (home / "config/interests.yaml").read_text() == before


def test_save_keeps_a_version_and_restores(client, home):
    path = home / "config/models.yaml"
    original = path.read_text()
    changed = original.replace("temperature: 0.5", "temperature: 0.6")
    resp = client.post("/settings/file/models", data={"text": changed}, follow_redirects=False)
    assert resp.status_code == 303
    assert path.read_text() == changed
    stamp = sorted((home / "data/history/models").glob("*.yaml"))[0].stem
    client.post(f"/settings/file/models/restore/{stamp}")
    assert path.read_text() == original


def test_models_reject_unknown_provider(client, home):
    text = (home / "config/models.yaml").read_text().replace("provider: deepseek", "provider: nope")
    assert "unknown provider" in client.post("/settings/file/models", data={"text": text}).text


def test_digest_form_keeps_comments(client, home):
    client.post("/settings/digest", data={"digest.notes": "9", "news.enabled": "1",
                                          "news.max_days": "5", "output.obsidian_dir": "",
                                          "device.name": "Mac"})
    text = (home / "config/settings.yaml").read_text()
    data = yaml.safe_load(text)
    assert data["digest"]["notes"] == 9 and data["news"]["enabled"] is True
    assert data["news"]["max_days"] == 5
    resp = client.post("/settings/digest", data={"news.max_days": "90"}, follow_redirects=False)
    assert "error" in resp.headers["location"]


def test_keys_are_masked(client, home):
    client.post("/settings/keys", data={"key": "TAVILY_API_KEY", "value": "tvly-abcdefgh1234"})
    assert "TAVILY_API_KEY=tvly-abcdefgh1234" in (home / ".env").read_text()
    page = client.get("/settings/keys").text
    assert "tvly-abcdefgh1234" not in page and "••••1234" in page
    assert client.post("/settings/keys", data={"key": "PATH", "value": "x"}).status_code == 400


def test_map_proposal_apply(client, home):
    cfg = Config()
    d = new_job_dir(cfg, "revise")
    (d / "meta.json").write_text(json.dumps({"kind": "revise", "status": "done", "pid": 0}))
    (d / "request.txt").write_text("add a topic")
    data = yaml.safe_load((home / "config/interests.yaml").read_text())
    data["topics"].append({"id": "video-gen", "name": "Video generation", "priority": "explore",
                           "summary": "Open video models."})
    (d / "proposal.yaml").write_text(yaml.safe_dump(data))
    (d / "changes.md").write_text("- added video generation")
    page = client.get("/settings/interests").text
    assert "Apply this revision" in page and "video-gen" in page
    client.post(f"/settings/interests/proposal/{d.name}/apply")
    assert "video-gen" in (home / "config/interests.yaml").read_text()
    assert "Apply this revision" not in client.get("/settings/interests").text


def test_revise_needs_a_key(client, home):
    (home / ".env").write_text("")
    resp = client.post("/settings/interests/revise", data={"request": "more RL", "web": "1"},
                       follow_redirects=False)
    assert "DEEPSEEK_API_KEY" in resp.headers["location"].replace("%20", " ") or \
        "DEEPSEEK_API_KEY" in client.get(resp.headers["location"]).text


def _answer(home, extra_topic=True, drop_first=False):
    data = yaml.safe_load((home / "config/interests.yaml").read_text())
    if drop_first:
        data["topics"] = data["topics"][1:]
    if extra_topic:
        data["topics"].append({"id": "video-gen", "name": "Video generation", "priority": "explore",
                               "summary": "Open video models.",
                               "queries": {"arxiv": ["video diffusion"]}})
    return ("Here is your revised map:\n\n```yaml\n" + yaml.safe_dump(data, sort_keys=False)
            + "```\n\n- Added video generation as an explore topic.\n")


def test_copy_prompt_carries_the_current_map(client, home):
    page = client.get("/settings/interests").text
    assert "Copy prompt" in page and 'data-copy="#copy-prompt"' in page
    assert "id: continual-learning" in page and "MY INTERESTS AND WHAT TO CHANGE" in page


def test_paste_replace(client, home):
    resp = client.post("/settings/interests/paste", data={"answer": _answer(home)},
                       follow_redirects=True)
    assert "Preview" in resp.text and "video-gen" in resp.text
    assert "Added video generation" in resp.text          # the model's notes are shown
    assert "video-gen" not in (home / "config/interests.yaml").read_text()  # nothing saved yet
    client.post("/settings/interests/draft/apply", data={"mode": "replace"})
    assert "video-gen" in (home / "config/interests.yaml").read_text()
    assert "Preview changes" in client.get("/settings/interests").text
    assert 'id="draft"' not in client.get("/settings/interests").text


def test_paste_merge_keeps_what_the_answer_left_out(client, home):
    client.post("/settings/interests/paste", data={"answer": _answer(home, drop_first=True)})
    page = client.get("/settings/interests").text
    assert "Not in the answer: post-training-rl" in page
    client.post("/settings/interests/draft/apply", data={"mode": "merge"})
    ids = [t["id"] for t in yaml.safe_load((home / "config/interests.yaml").read_text())["topics"]]
    assert "post-training-rl" in ids and "video-gen" in ids


def test_bad_paste_is_explained(client, home):
    page = client.post("/settings/interests/paste",
                       data={"answer": "```yaml\ntopics:\n  - id: Not A Slug\n```"}).text
    assert "not a usable map yet" in page and "lowercase slug" in page
    assert not (home / "data/drafts/interests.yaml").exists()


def test_discard_draft(client, home):
    client.post("/settings/interests/paste", data={"answer": _answer(home)})
    client.post("/settings/interests/draft/discard")
    assert 'id="draft"' not in client.get("/settings/interests").text
