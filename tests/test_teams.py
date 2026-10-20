import pytest

from kickoff_desk.teams import display_team_name


@pytest.mark.parametrize(
    "team_id,expected",
    [
        (57, "아스널"),
        (73, "토트넘"),
        (81, "바르셀로나"),
        (86, "레알 마드리드"),
        (5, "바이에른 뮌헨"),
        (65, "맨체스터 시티"),
    ],
)
def test_verified_team_identifiers_are_stable_across_source_name_changes(team_id, expected):
    assert display_team_name(team_id, "A changed provider name") == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Arsenal FC", "아스널"),
        ("Liverpool FC", "리버풀"),
        ("Manchester United FC", "맨체스터 유나이티드"),
        ("Tottenham Hotspur FC", "토트넘"),
        ("Club Atlético de Madrid", "아틀레티코 마드리드"),
        ("Club Atletico de Madrid", "아틀레티코 마드리드"),
        ("Athletic Club", "아틀레틱 빌바오"),
        ("FC Internazionale Milano", "인터 밀란"),
        ("Paris Saint-Germain FC", "파리 생제르맹"),
        ("FC Bayern München", "바이에른 뮌헨"),
        ("Borussia Dortmund", "보루시아 도르트문트"),
        ("Sporting Clube de Portugal", "스포르팅 CP"),
        ("Sporting Clube de Braga", "브라가"),
        ("AFC Ajax", "아약스"),
        ("Celtic FC", "셀틱"),
        ("Club Brugge KV", "클뤼프 브뤼허"),
        ("Fenerbahçe SK", "페네르바흐체"),
        ("FC Shakhtar Donetsk", "샤흐타르 도네츠크"),
        ("FC København", "FC 코펜하겐"),
        ("FK Bodø/Glimt", "보되 글림트"),
        ("PSG", "파리 생제르맹"),
        ("Man Utd", "맨체스터 유나이티드"),
    ],
)
def test_exact_formal_and_short_names_have_korean_labels_without_an_id(name, expected):
    assert display_team_name(None, name) == expected
    assert display_team_name(999_999, name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("  lIvErPoOl\t FC\n", "리버풀"),
        ("Manchester\u00a0United   FC", "맨체스터 유나이티드"),
        ("ＦＣ Ｂａｒｃｅｌｏｎａ", "바르셀로나"),
        ("Club Atle\u0301tico de Madrid", "아틀레티코 마드리드"),
    ],
)
def test_unicode_case_and_whitespace_variations_are_normalized(name, expected):
    assert display_team_name(None, name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Manchester",
        "United",
        "City",
        "Sporting",
        "Madrid",
        "Manchester United FC Women",
        "Arsenal FC U21",
        "Real Madrid Castilla",
        "FC Barcelona B",
        "Liverpool Montevideo",
        "Inter Miami CF",
        "Sporting Gijón",
        "Atlético Nacional",
        "Arsenal Tula",
        "FC Unknown Liverpool",
    ],
)
def test_partial_names_reserve_sides_and_other_clubs_are_not_confused(name):
    assert display_team_name(None, name) == name


@pytest.mark.parametrize("name", ["", "  Unlisted Club  ", "Future Club FC", "Team TBD"])
def test_unknown_team_names_preserve_the_original_text(name):
    assert display_team_name(999_999, name) == name


@pytest.mark.parametrize("name", ["토트넘", "  아스널 FC  ", "홈 팀 미정", "Arsenal (아스널)"])
def test_existing_korean_text_is_preserved_even_for_a_verified_id(name):
    assert display_team_name(57, name) == name


def test_only_the_explicitly_verified_ids_translate_unknown_names():
    assert display_team_name(66, "An unknown provider name") == "An unknown provider name"
    assert display_team_name(None, "A new club") == "A new club"
