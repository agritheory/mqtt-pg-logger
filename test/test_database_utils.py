from test.setup_test import SetupTest

from src.database_utils import DatabaseUtils


def test_load_commands():
    lines = [
        " ",
        " -- comment filtered out",
        "1 ",
        " 2 ",
        "3; ",
        "",
        "4;",
        "5; ",
        "",
        "6; ",
    ]

    SetupTest.ensure_test_dir()
    script_path = SetupTest.get_test_path("temp.sql")
    with open(script_path, "w") as f:
        f.write("\n".join(lines))

    commands = DatabaseUtils.load_commands(script_path)

    assert len(commands) == 4
    assert commands[0] == "1\n 2\n3;"
    assert commands[1] == "4;"
    assert commands[2] == "5;"
    assert commands[3] == "6;"
