"""@file mentions in the prompt (tui/mentions.py)."""

from src.tui.mentions import insert_mention, match_files, mention_at, mentioned_files

FILES = ["README.md", "api/main.py", "calc.py", "frontend/app/globals.css", "tests/test_calc.py"]


def test_the_mention_being_typed_is_found_before_the_cursor() -> None:
    assert mention_at("corrige @ca", 11) == (8, "ca")
    assert mention_at("@", 1) == (0, "")
    assert mention_at("mail me@home", 12) is None  # not after a space: an e-mail, not a mention
    assert mention_at("corrige @calc.py et", 19) is None  # the cursor left the mention


def test_files_are_ranked_name_first_then_path_then_letters_in_order() -> None:
    assert match_files(FILES, "calc") == ["calc.py", "tests/test_calc.py"]
    assert match_files(FILES, "glob") == ["frontend/app/globals.css"]
    assert match_files(FILES, "apim")[0] == "api/main.py"  # letters in order
    assert match_files(FILES, "zzz") == []
    assert match_files(FILES, "")[:3] == ["calc.py", "README.md", "api/main.py"]  # shallow first
    assert len(match_files([f"f{i}.py" for i in range(30)], "f")) == 8


def test_inserting_replaces_the_query_and_keeps_the_rest() -> None:
    assert insert_mention("corrige @ca", 11, 8, "calc.py") == ("corrige @calc.py ", 17)
    assert insert_mention("voir @ma puis", 8, 5, "api/main.py") == ("voir @api/main.py puis", 18)


def test_only_existing_files_count_as_mentions() -> None:
    text = "corrige @calc.py comme @tests/test_calc.py, pas @inconnu.py ni @calc.py"
    assert mentioned_files(text, FILES) == ["calc.py"]  # the trailing comma glues to the second one
    assert mentioned_files("voir @api/main.py et @calc.py", FILES) == ["api/main.py", "calc.py"]
