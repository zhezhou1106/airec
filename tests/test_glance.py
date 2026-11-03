from datetime import timedelta

from airec import glance, report
from airec.llm import Usage
from airec.sections.base import SectionResult, Window
from tests.conftest import NOW, ScriptedLlm

WINDOW = Window(NOW - timedelta(days=7), NOW)


def outcome(key, title, markdown):
    result = SectionResult(markdown, [f"{key} pick"], [], {})
    return report.SectionOutcome(key, title, WINDOW, result)


SECTIONS = [
    outcome("github_trends", "GitHub: rising repos", "- **[a/b](https://github.com/a/b)** agents"),
    outcome("ai_news", "AI news", "### Model releases\n- A model shipped"),
    outcome("papers", "Papers on your directions", "### Memory\n- paper one\n### Inference\n- two"),
]

ANSWER = {
    "repos": "Agent harnesses are\nhaving a moment.",
    "news": "Open weights keep coming.",
    "directions": [
        {"name": "Memory", "paragraph": "Forgetting is still unsolved."},
        {"name": "Inference", "paragraph": "Speculation goes local."},
    ],
}


def test_writes_one_paragraph_per_part_in_report_order(settings):
    llm = ScriptedLlm(settings, lambda *_: ANSWER)
    text = glance.write(llm, "a reader", WINDOW.label, SECTIONS)
    assert text == (
        "**Repos.** Agent harnesses are having a moment.\n\n"
        "**News.** Open weights keep coming.\n\n"
        "**Memory.** Forgetting is still unsolved.\n\n"
        "**Inference.** Speculation goes local."
    )
    role, system, user = llm.prompts[0]
    assert role == "writer"
    assert "a reader" in system and WINDOW.label in system
    assert "## AI news" in user and "### Memory" in user


def test_skips_parts_whose_section_is_missing(settings):
    llm = ScriptedLlm(settings, lambda *_: ANSWER)
    text = glance.write(llm, "a reader", WINDOW.label, SECTIONS[1:2])
    assert text == "**News.** Open weights keep coming."


def test_nothing_to_analyse_makes_no_call(settings):
    llm = ScriptedLlm(settings, lambda *_: ANSWER)
    failed = report.SectionOutcome("ai_news", "AI news", WINDOW, error="down")
    assert glance.write(llm, "a reader", WINDOW.label, [failed]) is None
    assert llm.prompts == []


def test_report_uses_the_analysis_and_falls_back_to_highlights():
    written = report.render("T", "2026-W45", NOW, SECTIONS, Usage(), "**News.** Analysis.")
    opening = written.split("## At a glance")[1].split("## GitHub")[0]
    assert "**News.** Analysis." in opening and "ai_news pick" not in opening

    plain = report.render("T", "2026-W45", NOW, SECTIONS, Usage())
    assert "- ai_news pick" in plain.split("## At a glance")[1]
