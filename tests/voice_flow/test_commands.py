from voice_flow.commands import match_command, Command

AGENTS = ["specter", "simon", "claw", "phil", "sam", "rex", "duke", "scout", "nova"]


def test_editing_commands():
    assert match_command("new line", AGENTS) == Command("newline")
    assert match_command("New Line.", AGENTS) == Command("newline")
    assert match_command("scratch that", AGENTS) == Command("scratch")
    assert match_command("select all", AGENTS) == Command("select_all")


def test_mode_and_route():
    assert match_command("switch to flow", AGENTS) == Command("set_mode", "flow")
    assert match_command("send to Specter", AGENTS) == Command("route", "specter")
    assert match_command("send to nova", AGENTS) == Command("route", "nova")


def test_unknown_agent_is_not_a_command():
    assert match_command("send to grandma", AGENTS) is None


def test_normal_dictation_passes_through():
    assert match_command("let us schedule a new line item for the invoice", AGENTS) is None
    assert match_command("the weather is nice today", AGENTS) is None
