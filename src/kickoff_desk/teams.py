"""Korean display names without modifying the API's original team names.

This is a curated alias catalogue, not a list of any season's participants.
Only complete names are matched: suffixes such as FC, B, U21 or Women are
never stripped automatically. Add an explicit alias when a provider uses a
different spelling, keeping unknown names available in their original form.
"""

from __future__ import annotations

import re
import unicodedata

_KNOWN_TEAM_IDS = {
    57: "아스널",
    73: "토트넘",
    81: "바르셀로나",
    86: "레알 마드리드",
    5: "바이에른 뮌헨",
    65: "맨체스터 시티",
}
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3\ud7b0-\ud7ff]")

# Names and common short forms; each tuple belongs to exactly one club.
_CLUB_ALIASES = {
    "아스널": ("Arsenal FC", "Arsenal"),
    "애스턴 빌라": ("Aston Villa FC", "Aston Villa"),
    "본머스": ("AFC Bournemouth", "Bournemouth"),
    "브렌트퍼드": ("Brentford FC", "Brentford"),
    "브라이턴": (
        "Brighton & Hove Albion FC",
        "Brighton & Hove Albion",
        "Brighton and Hove Albion",
        "Brighton",
    ),
    "첼시": ("Chelsea FC", "Chelsea"),
    "크리스털 팰리스": ("Crystal Palace FC", "Crystal Palace"),
    "에버턴": ("Everton FC", "Everton"),
    "풀럼": ("Fulham FC", "Fulham"),
    "리버풀": ("Liverpool FC", "Liverpool"),
    "맨체스터 시티": ("Manchester City FC", "Manchester City", "Man City"),
    "맨체스터 유나이티드": (
        "Manchester United FC",
        "Manchester United",
        "Man United",
        "Man Utd",
    ),
    "뉴캐슬 유나이티드": ("Newcastle United FC", "Newcastle United", "Newcastle"),
    "노팅엄 포리스트": ("Nottingham Forest FC", "Nottingham Forest"),
    "토트넘": ("Tottenham Hotspur FC", "Tottenham Hotspur", "Tottenham"),
    "웨스트햄 유나이티드": ("West Ham United FC", "West Ham United", "West Ham"),
    "울버햄프턴": ("Wolverhampton Wanderers FC", "Wolverhampton Wanderers", "Wolves"),
    "레스터 시티": ("Leicester City FC", "Leicester City"),
    "사우샘프턴": ("Southampton FC", "Southampton"),
    "입스위치 타운": ("Ipswich Town FC", "Ipswich Town"),
    "리즈 유나이티드": ("Leeds United FC", "Leeds United", "Leeds"),
    "번리": ("Burnley FC", "Burnley"),
    "선덜랜드": ("Sunderland AFC", "Sunderland"),
    "셰필드 유나이티드": ("Sheffield United FC", "Sheffield United"),
    "루턴 타운": ("Luton Town FC", "Luton Town"),
    "왓퍼드": ("Watford FC", "Watford"),
    "노리치 시티": ("Norwich City FC", "Norwich City"),
    "웨스트브로미치 앨비언": (
        "West Bromwich Albion FC",
        "West Bromwich Albion",
        "West Brom",
    ),
    "스토크 시티": ("Stoke City FC", "Stoke City"),
    "스완지 시티": ("Swansea City AFC", "Swansea City"),
    "카디프 시티": ("Cardiff City FC", "Cardiff City"),
    "퀸스 파크 레인저스": ("Queens Park Rangers FC", "Queens Park Rangers", "QPR"),
    "코번트리 시티": ("Coventry City FC",),
    "헐 시티": ("Hull City AFC",),
    "바르셀로나": ("FC Barcelona", "Barcelona", "Barça", "Barca"),
    "레알 마드리드": ("Real Madrid CF", "Real Madrid"),
    "아틀레티코 마드리드": (
        "Club Atlético de Madrid",
        "Club Atletico de Madrid",
        "Atlético de Madrid",
        "Atletico de Madrid",
        "Atlético Madrid",
        "Atletico Madrid",
    ),
    "아틀레틱 빌바오": ("Athletic Club", "Athletic Club Bilbao", "Athletic Bilbao"),
    "레알 소시에다드": ("Real Sociedad de Fútbol", "Real Sociedad de Futbol", "Real Sociedad"),
    "레알 베티스": ("Real Betis Balompié", "Real Betis Balompie", "Real Betis"),
    "세비야": ("Sevilla FC", "Sevilla"),
    "발렌시아": ("Valencia CF", "Valencia"),
    "비야레알": ("Villarreal CF", "Villarreal"),
    "헤타페": ("Getafe CF", "Getafe"),
    "오사수나": ("CA Osasuna", "Club Atlético Osasuna", "Osasuna"),
    "셀타 비고": ("RC Celta de Vigo", "RC Celta", "Celta de Vigo", "Celta Vigo"),
    "에스파뇰": ("RCD Espanyol de Barcelona", "RCD Espanyol", "Espanyol"),
    "마요르카": ("RCD Mallorca", "Mallorca"),
    "라요 바예카노": ("Rayo Vallecano de Madrid", "Rayo Vallecano"),
    "지로나": ("Girona FC", "Girona"),
    "알라베스": ("Deportivo Alavés", "Deportivo Alaves", "Alavés", "Alaves"),
    "라스팔마스": ("UD Las Palmas", "Las Palmas"),
    "레가네스": ("CD Leganés", "CD Leganes", "Leganés", "Leganes"),
    "레알 바야돌리드": ("Real Valladolid CF", "Real Valladolid"),
    "그라나다": ("Granada CF", "Granada"),
    "카디스": ("Cádiz CF", "Cadiz CF", "Cádiz", "Cadiz"),
    "알메리아": ("UD Almería", "UD Almeria", "Almería", "Almeria"),
    "엘체": ("Elche CF", "Elche"),
    "레반테": ("Levante UD", "Levante"),
    "레알 오비에도": ("Real Oviedo",),
    "데포르티보 라코루냐": (
        "RC Deportivo La Coruña",
        "RC Deportivo de La Coruña",
        "RC Deportivo de La Coruna",
        "Deportivo La Coruña",
        "Deportivo La Coruna",
    ),
    "바이에른 뮌헨": (
        "FC Bayern München",
        "FC Bayern Munchen",
        "FC Bayern Munich",
        "Bayern München",
        "Bayern Munich",
    ),
    "보루시아 도르트문트": ("Borussia Dortmund", "BV Borussia 09 Dortmund", "BVB"),
    "RB 라이프치히": ("RB Leipzig", "RasenBallsport Leipzig"),
    "바이어 레버쿠젠": (
        "Bayer 04 Leverkusen",
        "Bayer Leverkusen",
        "TSV Bayer 04 Leverkusen",
    ),
    "아인트라흐트 프랑크푸르트": ("Eintracht Frankfurt",),
    "슈투트가르트": ("VfB Stuttgart", "Stuttgart"),
    "프라이부르크": ("SC Freiburg", "Freiburg"),
    "마인츠": ("1. FSV Mainz 05", "FSV Mainz 05", "Mainz 05", "Mainz"),
    "볼프스부르크": ("VfL Wolfsburg", "Wolfsburg"),
    "보루시아 묀헨글라트바흐": (
        "Borussia Mönchengladbach",
        "Borussia Monchengladbach",
        "Borussia Moenchengladbach",
    ),
    "베르더 브레멘": ("SV Werder Bremen", "Werder Bremen"),
    "아우크스부르크": ("FC Augsburg", "Augsburg"),
    "호펜하임": ("TSG 1899 Hoffenheim", "TSG Hoffenheim", "1899 Hoffenheim", "Hoffenheim"),
    "우니온 베를린": ("1. FC Union Berlin", "FC Union Berlin", "Union Berlin"),
    "보훔": ("VfL Bochum 1848", "VfL Bochum", "Bochum"),
    "쾰른": ("1. FC Köln", "1. FC Koln", "1. FC Cologne", "FC Köln", "FC Cologne"),
    "함부르크": ("Hamburger SV", "Hamburg", "HSV"),
    "인터 밀란": (
        "FC Internazionale Milano",
        "Internazionale Milano",
        "Internazionale",
        "Inter Milan",
        "Inter",
    ),
    "AC 밀란": ("AC Milan", "Milan"),
    "유벤투스": ("Juventus FC", "Juventus"),
    "나폴리": ("SSC Napoli", "Napoli"),
    "AS 로마": ("AS Roma", "Roma"),
    "라치오": ("SS Lazio", "Lazio"),
    "아탈란타": ("Atalanta BC", "Atalanta"),
    "피오렌티나": ("ACF Fiorentina", "Fiorentina"),
    "볼로냐": ("Bologna FC 1909", "Bologna FC", "Bologna"),
    "토리노": ("Torino FC", "Torino"),
    "우디네세": ("Udinese Calcio", "Udinese"),
    "제노아": ("Genoa CFC", "Genoa"),
    "파르마": ("Parma Calcio 1913", "Parma"),
    "칼리아리": ("Cagliari Calcio", "Cagliari"),
    "레체": ("US Lecce", "Lecce"),
    "사수올로": ("US Sassuolo Calcio", "Sassuolo"),
    "코모": ("Como 1907", "Como"),
    "파리 생제르맹": (
        "Paris Saint-Germain FC",
        "Paris Saint-Germain",
        "Paris Saint Germain",
        "PSG",
    ),
    "올랭피크 마르세유": ("Olympique de Marseille", "Olympique Marseille", "Marseille"),
    "올랭피크 리옹": ("Olympique Lyonnais", "Lyon"),
    "AS 모나코": ("AS Monaco FC", "AS Monaco", "Monaco"),
    "릴": ("Lille OSC", "LOSC Lille", "Lille"),
    "니스": ("OGC Nice", "Nice"),
    "랑스": ("RC Lens", "Racing Club de Lens", "Lens"),
    "스타드 렌": ("Stade Rennais FC", "Stade Rennais", "Rennes"),
    "스트라스부르": ("RC Strasbourg Alsace", "RC Strasbourg", "Strasbourg"),
    "툴루즈": ("Toulouse FC", "Toulouse"),
    "낭트": ("FC Nantes", "Nantes"),
    "브레스트": ("Stade Brestois 29", "Stade Brestois", "Brest"),
    "오세르": ("AJ Auxerre", "Auxerre"),
    "벤피카": ("SL Benfica", "Sport Lisboa e Benfica", "Benfica"),
    "FC 포르투": ("FC Porto", "Porto"),
    "스포르팅 CP": ("Sporting Clube de Portugal", "Sporting CP", "Sporting Lisbon"),
    "브라가": ("Sporting Clube de Braga", "SC Braga", "Sporting Braga", "Braga"),
    "비토리아 기마랑이스": (
        "Vitória SC",
        "Vitoria SC",
        "Vitória de Guimarães",
        "Vitoria de Guimaraes",
    ),
    "아약스": ("AFC Ajax", "Ajax"),
    "PSV 에인트호번": ("PSV", "PSV Eindhoven"),
    "페예노르트": ("Feyenoord Rotterdam", "Feyenoord"),
    "AZ 알크마르": ("AZ", "AZ Alkmaar"),
    "트벤테": ("FC Twente '65", "FC Twente", "Twente"),
    "셀틱": ("Celtic FC", "Celtic"),
    "레인저스": ("Rangers FC", "Glasgow Rangers"),
    "클뤼프 브뤼허": ("Club Brugge KV", "Club Brugge"),
    "안더레흐트": ("RSC Anderlecht", "Anderlecht"),
    "헹크": ("KRC Genk", "Racing Genk", "Genk"),
    "위니옹 생질루아즈": (
        "Royale Union Saint-Gilloise",
        "Union Saint-Gilloise",
        "Union SG",
    ),
    "갈라타사라이": ("Galatasaray SK", "Galatasaray AŞ", "Galatasaray"),
    "페네르바흐체": ("Fenerbahçe SK", "Fenerbahce SK", "Fenerbahçe", "Fenerbahce"),
    "베식타시": ("Beşiktaş JK", "Besiktas JK", "Beşiktaş", "Besiktas"),
    "FC 바젤": ("FC Basel 1893", "FC Basel", "Basel"),
    "영 보이스": ("BSC Young Boys", "Young Boys"),
    "잘츠부르크": ("FC Red Bull Salzburg", "FC Salzburg", "Red Bull Salzburg", "RB Salzburg"),
    "슈투름 그라츠": ("SK Sturm Graz", "Sturm Graz"),
    "디나모 자그레브": ("GNK Dinamo Zagreb", "Dinamo Zagreb"),
    "올림피아코스": ("Olympiacos FC", "Olympiakos FC", "Olympiacos", "Olympiakos"),
    "파나티나이코스": ("Panathinaikos FC", "Panathinaikos"),
    "PAOK": ("PAOK FC", "PAOK Thessaloniki", "PAOK"),
    "츠르베나 즈베즈다": ("FK Crvena Zvezda", "Crvena Zvezda", "Red Star Belgrade"),
    "파르티잔": ("FK Partizan", "Partizan Belgrade"),
    "샤흐타르 도네츠크": ("FC Shakhtar Donetsk", "Shakhtar Donetsk", "FK Shakhtar Donetsk"),
    "디나모 키이우": ("FK Dynamo Kyiv", "FC Dynamo Kyiv", "Dynamo Kyiv", "Dynamo Kiev"),
    "스파르타 프라하": ("AC Sparta Praha", "Sparta Praha", "Sparta Prague"),
    "슬라비아 프라하": ("SK Slavia Praha", "Slavia Praha", "Slavia Prague"),
    "빅토리아 플젠": ("FC Viktoria Plzeň", "FC Viktoria Plzen", "Viktoria Plzeň", "Viktoria Plzen"),
    "FC 코펜하겐": ("FC København", "FC Kobenhavn", "FC Copenhagen", "København", "Copenhagen"),
    "미트윌란": ("FC Midtjylland", "Midtjylland"),
    "로센보르그": ("Rosenborg BK", "Rosenborg"),
    "LASK 린츠": ("LASK Linz",),
    "말라가": ("Málaga CF",),
    "라싱 산탄데르": ("Real Racing Club de Santander",),
    "바이킹 FK": ("Viking FK",),
    "보되 글림트": ("FK Bodø/Glimt", "FK Bodo/Glimt", "Bodø/Glimt", "Bodo/Glimt"),
}


def _normalize_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for korean_name, names in _CLUB_ALIASES.items():
        for name in names:
            key = _normalize_name(name)
            if key in aliases and aliases[key] != korean_name:
                raise ValueError("A team alias is assigned to more than one club.")
            aliases[key] = korean_name
    return aliases


_ALIASES = _build_aliases()


def display_team_name(team_id: int | None, name: str) -> str:
    """Return a familiar Korean label; preserve Korean and unrecognized names."""
    if _HANGUL.search(name):
        return name
    if team_id in _KNOWN_TEAM_IDS:
        return _KNOWN_TEAM_IDS[team_id]
    return _ALIASES.get(_normalize_name(name), name)
