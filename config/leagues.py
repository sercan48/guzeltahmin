"""League definitions — focused on 5 high-volume leagues."""

from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    code: str
    name: str
    country: str
    football_data_path: str
    tier: int  # 1 = top league
    openfootball_code: str = ""
    api_football_id: int = 0
    understat_code: str = ""  # Understat league slug
    has_xg: bool = False  # native xG data available


ACTIVE_LEAGUES: dict[str, League] = {
    "E0": League("E0", "Premier League", "England", "E0", 1, "en.1", 39, "EPL", True),
    "SP1": League("SP1", "La Liga", "Spain", "SP1", 1, "es.1", 140, "La_liga", True),
    "D1": League("D1", "Bundesliga", "Germany", "D1", 1, "de.1", 78, "Bundesliga", True),
    "I1": League("I1", "Serie A", "Italy", "I1", 1, "it.1", 135, "Serie_A", True),
    "T1": League("T1", "Süper Lig", "Turkey", "T1", 1, "tr.1", 203, "", False),
    "NORWAY_ELITESERIEN": League("NORWAY_ELITESERIEN", "Eliteserien", "Norway", "new/NOR", 1, "", 103, "", False),
    "BRAZIL_SERIE_A": League("BRAZIL_SERIE_A", "Serie A", "Brazil", "new/BRA", 1, "", 71, "", False),
}


ARCHIVE_LEAGUES: dict[str, League] = {
    "E1": League("E1", "Championship", "England", "E1", 2, "en.2", 40),
    "E2": League("E2", "League One", "England", "E2", 3, "en.3", 41),
    "E3": League("E3", "League Two", "England", "E3", 4, "en.4", 42),
    "SP2": League("SP2", "Segunda División", "Spain", "SP2", 2, "es.2", 141),
    "D2": League("D2", "2. Bundesliga", "Germany", "D2", 2, "de.2", 79),
    "I2": League("I2", "Serie B", "Italy", "I2", 2, "it.2", 136),
    "F1": League("F1", "Ligue 1", "France", "F1", 1, "fr.1", 61, "Ligue_1", True),
    "F2": League("F2", "Ligue 2", "France", "F2", 2, "fr.2", 62),
    "N1": League("N1", "Eredivisie", "Netherlands", "N1", 1, "nl.1", 88),
    "P1": League("P1", "Primeira Liga", "Portugal", "P1", 1, "pt.1", 94),
    "B1": League("B1", "Jupiler Pro League", "Belgium", "B1", 1, "be.1", 144),
    "SC0": League("SC0", "Scottish Premiership", "Scotland", "SC0", 1, "sco.1", 280),
    "G1": League("G1", "Super League", "Greece", "G1", 1, "gr.1", 197),
    "AT1": League("AT1", "Bundesliga", "Austria", "AT1", 1, "at.1", 218),
    "AT2": League("AT2", "2. Liga", "Austria", "AT2", 2, "at.2", 219),
}

ALL_LEAGUES = {**ACTIVE_LEAGUES, **ARCHIVE_LEAGUES}

# Backward-compat alias
MVP_LEAGUES = list(ACTIVE_LEAGUES.keys())

LEAGUE_EMOJI = {
    "E0": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "SP1": "🇪🇸", "D1": "🇩🇪", "I1": "🇮🇹", "T1": "🇹🇷",
    "BRAZIL_SERIE_A": "🇧🇷", "NORWAY_ELITESERIEN": "🇳🇴",
}

OPENFOOTBALL_SEASONS = [
    "2010-11", "2011-12", "2012-13", "2013-14", "2014-15",
    "2015-16", "2016-17", "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
    "2025-26",
]

OPENFOOTBALL_SEASON_MAP = {
    f"20{s[:2]}-{s[3:]}": f"20{s[:2]}-20{s[3:]}" for s in [
        "10-11", "11-12", "12-13", "13-14", "14-15",
        "15-16", "16-17", "17-18", "18-19", "19-20",
        "20-21", "21-22", "22-23", "23-24", "24-25", "25-26",
    ]
}

OPENFOOTBALL_BASE_URL = (
    "https://raw.githubusercontent.com/openfootball/football.json/master"
)


def get_league(code: str) -> League:
    """Get league by code from active or archive."""
    if code in ACTIVE_LEAGUES:
        return ACTIVE_LEAGUES[code]
    if code in ARCHIVE_LEAGUES:
        return ARCHIVE_LEAGUES[code]
    raise KeyError(f"Unknown league code: {code}")


def get_csv_url(season: str, league_code: str) -> str:
    """Build Football-Data.co.uk CSV download URL."""
    from config.settings import FOOTBALL_DATA_BASE_URL
    league = get_league(league_code)
    if league_code == "NORWAY_ELITESERIEN":
        return "https://www.football-data.co.uk/new/NOR.csv"
    if league_code == "BRAZIL_SERIE_A":
        return "https://www.football-data.co.uk/new/BRA.csv"
    return f"{FOOTBALL_DATA_BASE_URL}/{season}/{league.football_data_path}.csv"


def get_openfootball_url(season: str, league_code: str) -> str:
    """Build openfootball raw JSON URL."""
    league = get_league(league_code)
    if not league.openfootball_code:
        return ""
    return f"{OPENFOOTBALL_BASE_URL}/{season}/{league.openfootball_code}.json"


def is_active(code: str) -> bool:
    return code in ACTIVE_LEAGUES


LEAGUES = ALL_LEAGUES
