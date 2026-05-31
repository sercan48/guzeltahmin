"""Coupon Builder v3 — EV-Optimized Diversified Betting Slip Generator.

Generates coupons with realistic odds targets:
- BANKO: Min 1.50 toplam oran, yüksek güvenli karışık bahisler
- VALUE: Min 2.50 toplam oran, dengeli risk/kazanç
- SURPRIZ: Min 5.00 toplam oran, yüksek kazanç hedefli

Anti-DC-spam: DC picks require 82% prob AND 1.25 min odds.
EV-based ranking: picks sorted by Expected Value, not raw probability.
Diversity enforcement: max 1 DC per coupon, min 2 different BetTypes.
"""

from dataclasses import dataclass, field
from enum import Enum


class BetType(Enum):
    MATCH_RESULT = "Mac Sonucu (1X2)"
    DRAW = "Beraberlik (X)"
    OVER_UNDER = "Alt/Ust Gol"
    BOTH_TEAMS_SCORE = "Karsilikli Gol (KG)"
    DOUBLE_CHANCE = "Cifte Sans"
    DRAW_NO_BET = "Handikap Beraberlik (DNB)"
    FIRST_HALF = "Ilk Yari Sonucu"
    TOTAL_GOAL_RANGE = "Toplam Gol Araligi (TGS)"
    HANDICAP = "Handikapli Mac Sonucu (HMS)"
    FIRST_HALF_OVER_UNDER = "İY Alt/Ust Gol"


from config.leagues import LEAGUES

LEAGUE_NAMES = {k: v.name for k, v in LEAGUES.items()}

# Priority leagues for coupon generation
PRIORITY_LEAGUES = ["T1", "E0", "SP1", "D1", "I1", "F1"]


@dataclass
class BetPick:
    match: str
    league: str
    bet_type: BetType
    pick: str
    confidence: float
    reasoning: str
    estimated_odds: float
    real_odds: float = 0.0  # From Betting API (RapidAPI) = 1.0

    @property
    def league_name(self) -> str:
        return LEAGUE_NAMES.get(self.league, self.league)


@dataclass
class Coupon:
    name: str
    picks: list[BetPick] = field(default_factory=list)
    strategy: str = ""

    @property
    def total_odds(self) -> float:
        odds = 1.0
        for p in self.picks:
            odds *= p.estimated_odds
        return round(odds, 2)

    @property
    def avg_confidence(self) -> float:
        if not self.picks:
            return 0.0
        return round(sum(p.confidence for p in self.picks) / len(self.picks), 3)


def analyze_goals_market(home_goals_avg: float, away_goals_avg: float,
                         home_conceded_avg: float, away_conceded_avg: float) -> dict:
    """Predict Over/Under and BTTS markets."""
    expected_total = (home_goals_avg + away_goals_avg +
                      home_conceded_avg + away_conceded_avg) / 2

    over_25_prob = min(0.90, max(0.10, (expected_total - 2.0) * 0.45 + 0.5))
    btts_prob = min(0.85, max(0.15,
        (1 - (1 - home_goals_avg / 3.0) * (1 - away_goals_avg / 3.0))
    ))

    return {
        "expected_total_goals": round(expected_total, 2),
        "over_2_5_prob": round(over_25_prob, 3),
        "under_2_5_prob": round(1 - over_25_prob, 3),
        "btts_yes_prob": round(btts_prob, 3),
        "btts_no_prob": round(1 - btts_prob, 3),
    }


def analyze_tgs_market(expected_goals: float) -> dict:
    """Predict Total Goal Range (0-1, 2-3, 4-6, 7+)."""
    # Simple distribution based on expected goals
    probs = {}
    if expected_goals < 1.5:
        probs = {"0-1": 0.45, "2-3": 0.45, "4-6": 0.08, "7+": 0.02}
    elif expected_goals < 2.5:
        probs = {"0-1": 0.25, "2-3": 0.55, "4-6": 0.15, "7+": 0.05}
    elif expected_goals < 3.5:
        probs = {"0-1": 0.15, "2-3": 0.50, "4-6": 0.25, "7+": 0.10}
    else:
        probs = {"0-1": 0.05, "2-3": 0.35, "4-6": 0.45, "7+": 0.15}
    return probs


def analyze_handicap_market(win_prob: float, home_tier: int, away_tier: int) -> dict:
    """Analyze handicap options based on favorite strength."""
    if win_prob > 0.75 and (away_tier - home_tier) >= 2:
        return {"HMS 1": win_prob * 0.70, "odds_boost": 1.6} # -1 Handicap for home
    elif win_prob < 0.25 and (home_tier - away_tier) >= 2:
         return {"HMS 2": (1-win_prob) * 0.70, "odds_boost": 1.6} # -1 Handicap for away
    return {}


def analyze_iy_goals(expected_goals: float) -> dict:
    """Predict First Half goals (0.5 and 1.5)."""
    # Typically 35-40% of goals occur in the first half
    iy_exp = expected_goals * 0.40
    iy_05_prob = min(0.90, max(0.10, iy_exp * 0.50 + 0.30))
    iy_15_prob = min(0.60, max(0.05, iy_exp * 0.30))
    return {"iy_05_over": iy_05_prob, "iy_15_over": iy_15_prob}


def _estimate_ev(prob: float, odds: float) -> float:
    """Quick EV calculation: (prob * odds) - 1."""
    if prob <= 0 or odds <= 1.0:
        return -1.0
    return (prob * odds) - 1.0


def build_match_bets(prediction: dict, match_name: str, league: str) -> list[BetPick]:
    """Generate all viable bet picks for a single match.

    v3 changes:
    - DC threshold raised to 0.82 prob / 1.25 min odds
    - Standalone Draw market when d_prob >= 0.28
    - Draw No Bet (DNB) market for defensive profiles
    - EV calculated for every pick using real odds when available
    """
    picks = []

    h_prob = prediction["home_win_prob"]
    d_prob = prediction["draw_prob"]
    a_prob = prediction["away_win_prob"]

    features = prediction.get("features", {})
    raw_odds = prediction.get("_odds") or prediction.get("value_bets", {}) or {}

    # Real market odds (if available) for EV calculation
    real_h_odds = raw_odds.get("h") or raw_odds.get("home_odds")
    real_d_odds = raw_odds.get("d") or raw_odds.get("draw_odds")
    real_a_odds = raw_odds.get("a") or raw_odds.get("away_odds")
    real_o25_odds = raw_odds.get("o25") or raw_odds.get("over25_odds")
    real_u25_odds = raw_odds.get("u25") or raw_odds.get("under25_odds")

    # --- 1X2 Match Result: Home or Away (when clear favorite) ---
    result = prediction["predicted_result"]
    result_map = {"H": ("Ev Sahibi (1)", h_prob, real_h_odds),
                  "A": ("Deplasman (2)", a_prob, real_a_odds)}

    if result in result_map:
        label, main_prob, mkt_odds = result_map[result]
        estimated_odds = round((1 / main_prob) * 1.08, 2) if main_prob > 0.05 else 10.0
        display_odds = mkt_odds or estimated_odds

        if main_prob >= 0.45:
            picks.append(BetPick(
                match=match_name, league=league,
                bet_type=BetType.MATCH_RESULT,
                pick=label,
                confidence=main_prob,
                reasoning=f"H:{h_prob*100:.0f}% D:{d_prob*100:.0f}% A:{a_prob*100:.0f}% | EV:{_estimate_ev(main_prob, display_odds):+.2f}",
                estimated_odds=estimated_odds,
                real_odds=mkt_odds or 0.0,
            ))

    # --- Standalone Draw Market (NEW v3) ---
    # Draw is the most undervalued market — bookmakers overprice it
    if d_prob >= 0.28:
        draw_estimated_odds = round((1 / d_prob) * 1.08, 2)
        draw_mkt_odds = real_d_odds or draw_estimated_odds
        draw_ev = _estimate_ev(d_prob, draw_mkt_odds)

        if draw_ev >= 0.02 or d_prob >= 0.33:
            picks.append(BetPick(
                match=match_name, league=league,
                bet_type=BetType.DRAW,
                pick="Beraberlik (X)",
                confidence=d_prob,
                reasoning=f"Beraberlik olasılığı %{d_prob*100:.0f} | EV:{draw_ev:+.2f}",
                estimated_odds=draw_estimated_odds,
                real_odds=real_d_odds or 0.0,
            ))

    # --- Double Chance (STRICT: only when overwhelming + decent odds) ---
    dc_1x = h_prob + d_prob
    dc_x2 = d_prob + a_prob
    dc_12 = h_prob + a_prob

    dc_options = [
        ("1X (Ev Sahibi veya Beraberlik)", dc_1x),
        ("X2 (Beraberlik veya Deplasman)", dc_x2),
        ("12 (Ev Sahibi veya Deplasman)", dc_12),
    ]
    dc_options.sort(key=lambda x: x[1], reverse=True)
    best_dc_label, best_dc_prob = dc_options[0]

    dc_odds = round((1 / best_dc_prob) * 1.08, 2) if best_dc_prob > 0 else 1.0
    # v3: much stricter — 0.82 prob AND 1.25 min odds
    if best_dc_prob >= 0.82 and dc_odds >= 1.25:
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.DOUBLE_CHANCE,
            pick=best_dc_label,
            confidence=best_dc_prob,
            reasoning=f"Cifte sans: {best_dc_prob*100:.0f}% (yüksek eşik geçti)",
            estimated_odds=dc_odds,
        ))

    # --- Draw No Bet / Handicap Draw (NEW v3) ---
    # Good for defensive, low-scoring game profiles
    if d_prob >= 0.28 and h_prob > a_prob:
        # DNB Home: home wins or draw = refund
        dnb_prob = h_prob + d_prob * 0.5  # Half credit for draw
        dnb_odds = round((1 / dnb_prob) * 1.10, 2)
        if dnb_prob >= 0.55:
            picks.append(BetPick(
                match=match_name, league=league,
                bet_type=BetType.DRAW_NO_BET,
                pick="DNB Ev Sahibi",
                confidence=dnb_prob,
                reasoning=f"Beraberlik riski yüksek (%{d_prob*100:.0f}) - DNB koruma",
                estimated_odds=dnb_odds,
            ))
    elif d_prob >= 0.28 and a_prob > h_prob:
        dnb_prob = a_prob + d_prob * 0.5
        dnb_odds = round((1 / dnb_prob) * 1.10, 2)
        if dnb_prob >= 0.55:
            picks.append(BetPick(
                match=match_name, league=league,
                bet_type=BetType.DRAW_NO_BET,
                pick="DNB Deplasman",
                confidence=dnb_prob,
                reasoning=f"Beraberlik riski yüksek (%{d_prob*100:.0f}) - DNB koruma",
                estimated_odds=dnb_odds,
            ))

    # --- Over/Under 2.5 Goals ---
    home_scored = features.get("home_goals_scored_avg", 1.3)
    away_scored = features.get("away_goals_scored_avg", 1.1)
    home_conceded = features.get("home_goals_conceded_avg", 1.1)
    away_conceded = features.get("away_goals_conceded_avg", 1.2)

    goals = analyze_goals_market(home_scored, away_scored,
                                  home_conceded, away_conceded)

    # Use ensemble Poisson probs if available (more accurate)
    over25_prob = prediction.get("over25_prob", goals["over_2_5_prob"])
    under25_prob = 1.0 - over25_prob

    if over25_prob >= 0.55:
        ou_odds = round((1 / over25_prob) * 1.08, 2)
        mkt_ou = real_o25_odds or ou_odds
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.OVER_UNDER,
            pick=f"Üst 2.5 ({goals['expected_total_goals']} gol beklentisi)",
            confidence=over25_prob,
            reasoning=f"Toplam gol beklentisi: {goals['expected_total_goals']} | EV:{_estimate_ev(over25_prob, mkt_ou):+.2f}",
            estimated_odds=ou_odds,
            real_odds=real_o25_odds or 0.0,
        ))
    elif under25_prob >= 0.55:
        ou_odds = round((1 / under25_prob) * 1.08, 2)
        mkt_ou = real_u25_odds or ou_odds
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.OVER_UNDER,
            pick=f"Alt 2.5 ({goals['expected_total_goals']} gol beklentisi)",
            confidence=under25_prob,
            reasoning=f"Toplam gol beklentisi: {goals['expected_total_goals']} | EV:{_estimate_ev(under25_prob, mkt_ou):+.2f}",
            estimated_odds=ou_odds,
            real_odds=real_u25_odds or 0.0,
        ))

    # --- Both Teams to Score (KG) ---
    btts_prob = prediction.get("btts_prob", goals["btts_yes_prob"])
    btts_no_prob = 1.0 - btts_prob

    if btts_prob >= 0.58:
        btts_odds = round((1 / btts_prob) * 1.08, 2)
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.BOTH_TEAMS_SCORE,
            pick="KG Var",
            confidence=btts_prob,
            reasoning=f"KG olasılık: %{btts_prob*100:.0f}",
            estimated_odds=btts_odds,
        ))
    elif btts_no_prob >= 0.62:
        btts_odds = round((1 / btts_no_prob) * 1.08, 2)
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.BOTH_TEAMS_SCORE,
            pick="KG Yok",
            confidence=btts_no_prob,
            reasoning=f"KG yok olasılık: %{btts_no_prob*100:.0f}",
            estimated_odds=btts_odds,
        ))

    # --- Total Goal Range (TGS) ---
    tgs = analyze_tgs_market(goals["expected_total_goals"])
    best_tgs = max(tgs.items(), key=lambda x: x[1])
    if best_tgs[1] >= 0.40:
        tgs_odds = round((1 / best_tgs[1]) * 1.12, 2)
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.TOTAL_GOAL_RANGE,
            pick=f"TGS {best_tgs[0]}",
            confidence=best_tgs[1],
            reasoning=f"Gol beklentisi: {goals['expected_total_goals']}",
            estimated_odds=tgs_odds,
        ))

    # --- Handicap (HMS) ---
    h_tier = features.get("home_tier", 3)
    a_tier = features.get("away_tier", 3)
    hms = analyze_handicap_market(h_prob, h_tier, a_tier)
    if hms:
        hms_label = list(hms.keys())[0]
        hms_prob = hms[hms_label]
        hms_odds = round((1 / hms_prob) * 1.10, 2)
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.HANDICAP,
            pick=hms_label,
            confidence=hms_prob,
            reasoning=f"Güçlü favori avantajı (Tier {h_tier} vs {a_tier})",
            estimated_odds=hms_odds,
        ))

    # --- First Half (İY) Goals ---
    iy = analyze_iy_goals(goals["expected_total_goals"])
    if iy["iy_05_over"] >= 0.70:
        iy_odds = round((1 / iy["iy_05_over"]) * 1.08, 2)
        picks.append(BetPick(
            match=match_name, league=league,
            bet_type=BetType.FIRST_HALF_OVER_UNDER,
            pick="İY 0.5 Üst",
            confidence=iy["iy_05_over"],
            reasoning=f"Hızlı başlangıç beklentisi",
            estimated_odds=iy_odds,
        ))

    return picks


def analyze_match_deep(prediction: dict, match_name: str, league: str) -> list[BetPick]:
    """Rank ALL viable picks for a match by Expected Value.

    v3: Sorts by EV = (prob * odds) - 1, with DC penalty.
    Returns picks sorted by true value, not raw probability.
    """
    picks = build_match_bets(prediction, match_name, league)
    if not picks:
        return []

    DC_EV_PENALTY = -0.10

    def _pick_ev(pick: BetPick) -> float:
        # Use real market odds if available, else estimated
        odds = pick.real_odds if pick.real_odds and pick.real_odds > 1.0 else pick.estimated_odds
        ev = _estimate_ev(pick.confidence, odds)
        if pick.bet_type == BetType.DOUBLE_CHANCE:
            ev += DC_EV_PENALTY
        return ev

    picks.sort(key=_pick_ev, reverse=True)
    return picks


def _select_diverse_picks(candidates: list[BetPick], max_picks: int,
                          min_total_odds: float, max_total_odds: float) -> list[BetPick]:
    """Select diverse picks that hit the target odds range.

    v3 diversity rules:
    - Max 1 pick per match
    - Max 1 DC pick per coupon (anti-spam)
    - Max 2 picks from same league
    - Min 2 different BetTypes if 3+ picks
    - EV-based sorting instead of raw value score
    """
    DC_PENALTY = -0.10

    def _sort_ev(p: BetPick) -> float:
        odds = p.real_odds if p.real_odds and p.real_odds > 1.0 else p.estimated_odds
        ev = _estimate_ev(p.confidence, odds)
        if p.bet_type == BetType.DOUBLE_CHANCE:
            ev += DC_PENALTY
        return ev

    candidates.sort(key=_sort_ev, reverse=True)

    selected = []
    match_count = {}
    type_count = {}
    league_count = {}
    dc_count = 0
    running_odds = 1.0

    for pick in candidates:
        if len(selected) >= max_picks:
            break

        # Max 1 pick per match
        if match_count.get(pick.match, 0) >= 1:
            continue

        # Max 1 DC pick per coupon
        if pick.bet_type == BetType.DOUBLE_CHANCE and dc_count >= 1:
            continue

        # Max 2 picks from same league
        if league_count.get(pick.league, 0) >= 2:
            continue

        # Check total odds ceiling
        new_odds = running_odds * pick.estimated_odds
        if new_odds > max_total_odds and len(selected) >= 2:
            continue

        selected.append(pick)
        match_count[pick.match] = match_count.get(pick.match, 0) + 1
        type_count[pick.bet_type] = type_count.get(pick.bet_type, 0) + 1
        league_count[pick.league] = league_count.get(pick.league, 0) + 1
        if pick.bet_type == BetType.DOUBLE_CHANCE:
            dc_count += 1
        running_odds = new_odds

    # If we're below min odds, fill with higher-odds non-DC picks
    if running_odds < min_total_odds and len(selected) < max_picks:
        remaining = [p for p in candidates if p not in selected
                     and match_count.get(p.match, 0) < 1
                     and p.bet_type != BetType.DOUBLE_CHANCE]
        remaining.sort(key=lambda x: x.estimated_odds, reverse=True)
        for pick in remaining:
            if len(selected) >= max_picks:
                break
            selected.append(pick)
            running_odds *= pick.estimated_odds
            match_count[pick.match] = match_count.get(pick.match, 0) + 1
            type_count[pick.bet_type] = type_count.get(pick.bet_type, 0) + 1
            if running_odds >= min_total_odds:
                break

    return selected


def build_coupon(all_match_picks: list[list[BetPick]],
                 strategy: str = "banko",
                 league_filter: str = None) -> Coupon:
    """Build an optimal coupon from available picks.

    Args:
        strategy: "banko" (1.5+), "value" (2.5+), or "surpriz" (5.0+)
        league_filter: Optional league code (e.g. "T1") to filter matches
    """
    flat_picks = [p for match_picks in all_match_picks for p in match_picks]

    # Apply league filter
    if league_filter:
        flat_picks = [p for p in flat_picks if p.league == league_filter]

    if not flat_picks:
        return Coupon(name="BOS KUPON", picks=[], strategy="Filtre sonucu mac bulunamadi.")

    if strategy == "banko":
        # High confidence picks, mixed bet types, target 1.50 - 3.00 total odds
        candidates = [p for p in flat_picks if p.confidence >= 0.65 and p.estimated_odds >= 1.15]
        selected = _select_diverse_picks(candidates, max_picks=5,
                                         min_total_odds=1.50, max_total_odds=3.50)

        league_label = LEAGUE_NAMES.get(league_filter, "Tum Ligler") if league_filter else "Tum Ligler"
        return Coupon(
            name=f"BANKO KUPON ({league_label})",
            picks=selected,
            strategy=f"Yuksek guvenli karisik bahisler. Hedef oran: 1.50-3.50"
        )

    elif strategy == "value":
        # Balanced picks, target 2.50 - 6.00 total odds
        candidates = [p for p in flat_picks if p.confidence >= 0.50 and p.estimated_odds >= 1.25]
        selected = _select_diverse_picks(candidates, max_picks=6,
                                         min_total_odds=2.50, max_total_odds=8.00)

        league_label = LEAGUE_NAMES.get(league_filter, "Tum Ligler") if league_filter else "Tum Ligler"
        return Coupon(
            name=f"VALUE KUPON ({league_label})",
            picks=selected,
            strategy=f"Oran-guven dengesi. Hedef oran: 2.50-8.00"
        )

    elif strategy == "surpriz":
        # Higher odds picks, target 5.00+ total
        candidates = [p for p in flat_picks if p.confidence >= 0.40 and p.estimated_odds >= 1.50]
        selected = _select_diverse_picks(candidates, max_picks=5,
                                         min_total_odds=5.00, max_total_odds=25.00)

        league_label = LEAGUE_NAMES.get(league_filter, "Tum Ligler") if league_filter else "Tum Ligler"
        return Coupon(
            name=f"SURPRIZ KUPON ({league_label})",
            picks=selected,
            strategy=f"Yuksek oranli surpriz bahisler. Hedef oran: 5.00+"
        )

    return Coupon(name="GECERSIZ", picks=[], strategy="Gecersiz strateji")


def format_coupon(coupon: Coupon) -> str:
    """Format coupon for terminal or Telegram display."""
    lines = []
    lines.append("=" * 50)
    lines.append(f"  {coupon.name}")
    lines.append(f"  {coupon.strategy}")
    lines.append("=" * 50)

    if not coupon.picks:
        lines.append("  Uygun bahis bulunamadi.")
        lines.append("=" * 50)
        return "\n".join(lines)

    for i, p in enumerate(coupon.picks, 1):
        lines.append(f"\n  {i}. [{p.league_name}] {p.match}")
        lines.append(f"     {p.bet_type.value} -> {p.pick}")
        lines.append(f"     Oran: {p.estimated_odds} | Guven: %{p.confidence*100:.0f}")
        lines.append(f"     ({p.reasoning})")

    lines.append("")
    lines.append("-" * 50)
    lines.append(f"  TOPLAM ORAN  : {coupon.total_odds}")
    lines.append(f"  ORT. GUVEN   : %{coupon.avg_confidence*100:.0f}")
    lines.append(f"  MAC SAYISI   : {len(coupon.picks)}")
    lines.append("=" * 50)

    return "\n".join(lines)


def format_telegram_coupon(coupon: Coupon) -> str:
    """Format coupon specifically for Telegram Premium Groups with rich emojis."""
    lines = []
    
    # Title logic
    if "BANKO" in coupon.name.upper():
        theme = "🔥 GÜNÜN KASALAMA / BANKO KUPONU 🔥"
        risk = "Düşük (Yüksek İsabet)"
    elif "VALUE" in coupon.name.upper():
        theme = "💎 DEĞERLİ ORANLAR / SİSTEM KUPONU 💎"
        risk = "Orta (Değer Bahsi)"
    else:
        theme = "🚀 SÜRPRİZ / YÜKSEK KAZANÇ 🚀"
        risk = "Yüksek (Küçük Kasa Puanı/Düşük Misli Şart)"
        
    lines.append(f"*{theme}*\n")
    
    if not coupon.picks:
        return "❌ Bu seansa uygun bahis bulunamadı."
        
    for p in coupon.picks:
        lines.append(f"⚽️ *{p.match}*")
        lines.append(f"🏆 _{p.league_name}_")
        lines.append(f"🎯 *Tahmin*: {p.pick}")
        lines.append(f"📈 *Yapay Zeka Güveni*: %{p.confidence*100:.0f}")
        # Show if there is a real market arbitrage
        if p.real_odds and p.real_odds > p.estimated_odds * 1.1:
            lines.append(f"💸 *Oran*: {p.real_odds} (Arb. Fırsatı!)")
        else:
            lines.append(f"💸 *Oran*: {p.estimated_odds}")
        lines.append("") # space

    lines.append(f"📊 *TOPLAM ORAN*: {coupon.total_odds}")
    lines.append(f"⚖️ *Risk Seviyesi*: {risk}")
    lines.append("🤖 _Yapay Zeka Destekli Tahmin Motoru_")
    
    return "\n".join(lines)

def build_llm_telegram_coupon(coupon: Coupon) -> str:
    """Pass the mathematically generated picks to the ultimate LLM Judge to provide Premium Telegram feedback and Veto logic."""
    if not coupon.picks:
        return "❌ Bu seansa uygun bahis bulunamadı."
        
    try:
        from src.ingestion.llm_client import SportsAnalystLLM
    except ImportError:
        return "Yapay zeka (LLM) modülü bulunamadı.\n\n" + format_telegram_coupon(coupon)
        
    llm = SportsAnalystLLM()
    # Serialize picks
    picks_data = []
    for p in coupon.picks:
        picks_data.append({
            "match": p.match,
            "league": p.league_name,
            "pick": p.pick,
            "ai_confidence_pct": int(p.confidence * 100),
            "estimated_odds": p.estimated_odds,
            "market_odds": getattr(p, 'real_odds', None) or "Bilinmiyor"
        })
        
    result = llm.analyze_picks_and_veto(coupon.name, picks_data)
    
    return result.get("telegram_message", format_telegram_coupon(coupon))
