"""Planner tests. No OpenMinion runtime required."""

from release_agent.scripted import (
    CompletedTool,
    TextReply,
    ToolRequest,
    extract_block,
    plan_release_turn,
    unwrap_tool_output,
)


def test_draft_reads_then_compiles_then_returns_json():
    user = "stage: draft\nchangelog_path: /game/CHANGELOG.md\nbug_log_path: /game/bugs.md\n"
    offered = {"read_release_file", "compile_release_packet"}

    first = plan_release_turn(offered_tools=offered, user_text=user, completed=[])
    assert isinstance(first, ToolRequest)
    assert first.arguments == {"path": "/game/CHANGELOG.md"}

    second = plan_release_turn(
        offered_tools=offered,
        user_text=user,
        completed=[CompletedTool("read_release_file", {"path": "/game/CHANGELOG.md"}, "log")],
    )
    assert isinstance(second, ToolRequest)
    assert second.arguments == {"path": "/game/bugs.md"}

    third = plan_release_turn(
        offered_tools=offered,
        user_text=user,
        completed=[
            CompletedTool("read_release_file", {"path": "/game/CHANGELOG.md"}, "log"),
            CompletedTool("read_release_file", {"path": "/game/bugs.md"}, "bugs"),
        ],
    )
    assert isinstance(third, ToolRequest)
    assert third.name == "compile_release_packet"
    assert third.arguments["changelog_text"] == "log"

    final = plan_release_turn(
        offered_tools=offered,
        user_text=user,
        completed=[
            CompletedTool("compile_release_packet", {}, '{\n  "game": "Emberfall"\n}'),
        ],
    )
    assert isinstance(final, TextReply)
    assert final.text.startswith("{")


def test_draft_does_not_offer_a_write_and_publish_does():
    draft = plan_release_turn(
        offered_tools={"read_release_file", "compile_release_packet", "write_release_files"},
        user_text="stage: draft\nchangelog_path: /a\nbug_log_path: /b\n",
        completed=[],
    )
    assert isinstance(draft, ToolRequest)
    assert draft.name == "read_release_file"

    user = (
        "stage: publish\n"
        "output_dir: /out\n"
        "<<<RELEASE_MARKDOWN>>>\n"
        "# notes\n"
        "<<<END_RELEASE_MARKDOWN>>>\n"
        "<<<RELEASE_JSON>>>\n"
        "{}\n"
        "<<<END_RELEASE_JSON>>>\n"
    )
    publish = plan_release_turn(
        offered_tools={"write_release_files"},
        user_text=user,
        completed=[],
    )
    assert isinstance(publish, ToolRequest)
    assert publish.name == "write_release_files"
    assert publish.arguments["markdown"] == "# notes\n"
    assert publish.arguments["payload_json"] == "{}\n"
    assert extract_block(user, "RELEASE_MARKDOWN") == "# notes\n"


def test_unrelated_provider_call_is_left_to_echo():
    assert (
        plan_release_turn(
            offered_tools={"submit_output"},
            user_text="stage: draft\nchangelog_path: /a\n",
            completed=[],
        )
        is None
    )
    assert (
        plan_release_turn(
            offered_tools={"read_release_file"},
            user_text="Classify freshness sensitivity.",
            completed=[],
        )
        is None
    )


def test_unwrap_tool_envelope():
    raw = '{"status": "success", "outputs": {"data": "contents of hello.txt"}}'
    assert unwrap_tool_output(raw) == "contents of hello.txt"
    assert unwrap_tool_output("plain text") == "plain text"
