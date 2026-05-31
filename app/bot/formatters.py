"""Telegram message formatting — odds-centric, professional, expert-friendly."""

from config.leagues import LEAGUE_EMOJI


def progress_bar(value: float, max_val: float = 100, length: int = 10) -> str:
    filled = int(round(length * value / max(max_val, 1)))
    filled = max(0, min(filled, length))
    return "█" * filled + "░" * (length - filled)


def confidence_emoji(conf: float) -> str:
    if conf >= 80:
        return "🔥"
    elif conf >= 65:
        return "✅"
    elif conf >= 50:
        return "⚡"
    return "⚠️"


def result_emoji(result: str) -> str:
    return {"H": "🏠", "D": "🤝", "A": "✈️"}.get(result, "❓")


def _value_tag(ev: float) -> str:
    if ev >= 0.15:
        return "🔥 YÜKSEK DEĞER"
    elif ev >= 0.08:
        return "💎 GÜÇLÜ DEĞER"
    elif ev >= 0.02:
        return "✅ DEĞER"
    return ""


def _odds_display(odds_val) -> str:
    """Format odds for display, handle None."""
    if odds_val is None or odds_val == 0:
        return "—"
    return f"{odds_val:.2f}"


# ─────────────────────────────────────
# PRIMARY: ODDS-CENTRIC DAILY POST
# ─────────────────────────────────────

def format_odds_centric_daily(predictions: list, accuracy_stats: dict) -> str:
    """Premium channel daily post — odds-centric layout.

    Priority order for each match:
      1. Market odds & model comparison
      2. Value/EV signal
      3. Probability distribution
      4. Secondary markets (O/U 2.5, BTTS)
      5. Confidence (last, not first)
    """
    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    overall_acc = accuracy_stats.get("overall", 0)
    last7_acc = accuracy_stats.get("last_7_days", 0)

    lines = [
        f"📊 GÜNÜN ODDS ANALİZİ — {today}",
        f"{'━' * 32}",
        f"📈 7 Gün: %{last7_acc:.0f} | Genel: %{overall_acc:.0f}",
        f"{'━' * 32}",
    ]

    # Group by league
    by_league: dict[str, list] = {}
    for pred in predictions:
        lc = pred.get("league_code", "??")
        by_league.setdefault(lc, []).append(pred)

    for league_code, preds in by_league.items():
        league_flag = LEAGUE_EMOJI.get(league_code, "⚽")
        lines.append(f"\n{league_flag} {league_code}")
        lines.append(f"{'─' * 28}")

        # Sort by confidence descending within league
        preds.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        for pred in preds:
            lines.append(_format_odds_card(pred))

    lines.extend([
        f"\n{'━' * 32}",
        "🤖 Güzel Tahmin | Ensemble AI",
        "⚠️ Yatırım tavsiyesi değildir.",
    ])

    return "\n".join(lines)


def _format_odds_card(pred: dict) -> str:
    """Single match card — odds-centric layout matching user specifications."""
    home = pred.get("home_team", "?")
    away = pred.get("away_team", "?")
    result = pred.get("predicted_result", "?")
    conf = pred.get("confidence", 0)
    h_prob = pred.get("h_prob", pred.get("home_win_prob", 0)) or 0
    d_prob = pred.get("d_prob", pred.get("draw_prob", 0)) or 0
    a_prob = pred.get("a_prob", pred.get("away_win_prob", 0)) or 0

    league_code = pred.get("league_code", "??")
    league_flag = LEAGUE_EMOJI.get(league_code, "⚽")

    result_label = {"H": "1 (Ev Sahibi)", "D": "X (Beraberlik)", "A": "2 (Deplasman)"}.get(result, result)
    ce = confidence_emoji(conf)

    odds = pred.get("_odds") or {}
    h_odds = odds.get("h")
    d_odds = odds.get("d")
    a_odds = odds.get("a")

    pick_odds_val = {"H": h_odds, "D": d_odds, "A": a_odds}.get(result)
    pick_odds_str = _odds_display(pick_odds_val)

    # EV calculation
    ev_str = ""
    if h_odds and d_odds and a_odds:
        ev = h_prob * h_odds - 1 if result == "H" else (d_prob * d_odds - 1 if result == "D" else a_prob * a_odds - 1)
        if ev > 0:
            ev_str = f" | Değer (EV): {ev*100:+.1f}%"

    lines = [
        f"\n{league_flag} {league_code} | {home} vs {away}",
        f"🎯 ANA TAHMİN: {result_label}",
        f"{ce} Güven: %{conf:.0f} | Oran: {pick_odds_str}{ev_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Piyasa Oranları: 1={_odds_display(h_odds)} | X={_odds_display(d_odds)} | 2={_odds_display(a_odds)}",
        f"🧠 Model İhtimali: 1=%{h_prob*100:.0f} | X=%{d_prob*100:.0f} | 2=%{a_prob*100:.0f}"
    ]

    # Optional xG
    hl = pred.get("home_lambda")
    al = pred.get("away_lambda")
    if hl is not None and al is not None:
        lines.append(f"🎯 xG Beklentisi: {hl:.1f} – {al:.1f}")

    # Alternatives Row
    o25 = pred.get("over25_prob")
    btts = pred.get("btts_prob")
    if o25 or btts:
        lines.append("────────────────────────────")
        alt_parts = []
        if o25:
            o25_odds = odds.get("o25")
            o25_odds_str = f" @{o25_odds:.2f}" if o25_odds else ""
            alt_parts.append(f"Ü2.5 (%{o25*100:.0f}){o25_odds_str}")
        if btts:
            btts_odds = odds.get("btts") or odds.get("btts_y")
            btts_odds_str = f" @{btts_odds:.2f}" if btts_odds else ""
            alt_parts.append(f"KG Var (%{btts*100:.0f}){btts_odds_str}")
        
        lines.append(f"💡 Alternatifler: {' | '.join(alt_parts)}")

    return "\n".join(lines)


# ─────────────────────────────────────
# SINGLE MATCH (for /tahmin command)
# ─────────────────────────────────────

def format_prediction(pred: dict) -> str:
    """Format a single match prediction — odds-aware."""
    home = pred.get("home_team", "?")
    away = pred.get("away_team", "?")
    result = pred.get("predicted_result", "?")
    conf = pred.get("confidence", 0)
    h_prob = pred.get("h_prob", pred.get("home_win_prob", 0))
    d_prob = pred.get("d_prob", pred.get("draw_prob", 0))
    a_prob = pred.get("a_prob", pred.get("away_win_prob", 0))

    result_label = {"H": "Ev Sahibi", "D": "Berabere", "A": "Deplasman"}.get(result, result)
    ce = confidence_emoji(conf)
    re = result_emoji(result)

    lines = [
        f"⚽ {home} 🆚 {away}",
        f"📊 Tahmin: {re} {result_label}",
        f"📈 H:%{h_prob*100:.0f} | D:%{d_prob*100:.0f} | A:%{a_prob*100:.0f}",
        f"{ce} Güven: %{conf:.0f} {progress_bar(conf)}",
    ]

    if pred.get("model_agreement"):
        agr = pred["model_agreement"]
        lines.append(f"🔗 Model Uyumu: {agr:.0%}")

    if pred.get("over25_prob"):
        o25 = pred["over25_prob"]
        btts = pred.get("btts_prob", 0)
        lines.append(f"📉 Ü2.5: %{o25*100:.0f} | KG: %{btts*100:.0f}")

    if pred.get("value_margin") and pred["value_margin"] > 2:
        lines.append(f"💰 Value: +{pred['value_margin']:.1f}% marj")

    if pred.get("home_lambda") and pred.get("away_lambda"):
        lines.append(f"🎯 xG: {pred['home_lambda']:.1f} – {pred['away_lambda']:.1f}")

    return "\n".join(lines)


# ─────────────────────────────────────
# COUPON
# ─────────────────────────────────────

def format_coupon(picks: list, strategy: str, total_odds: float) -> str:
    """Format a complete coupon."""
    strategy_names = {
        "banko": "🎯 BANKO KUPON",
        "value": "💰 VALUE KUPON",
        "surpriz": "🎲 SÜRPRİZ KUPON",
        "custom": "📋 ÖZEL KUPON",
    }
    header = strategy_names.get(strategy, "📋 KUPON")

    lines = [
        f"{'━'*28}",
        f"  {header}",
        f"{'━'*28}",
    ]

    for i, pick in enumerate(picks, 1):
        home = pick.get("home_team", "?")
        away = pick.get("away_team", "?")
        bet = pick.get("bet_label", pick.get("bet_type", "?"))
        odds = pick.get("odds", 0)
        conf = pick.get("confidence", 0)
        ce = confidence_emoji(conf)

        lines.append(f"\n{i}. ⚽ {home} vs {away}")
        lines.append(f"   {ce} {bet} @ {odds:.2f} (G:%{conf:.0f})")

    lines.extend([
        f"\n{'─'*28}",
        f"📊 Toplam Oran: {total_odds:.2f}",
        f"💡 Maç Sayısı: {len(picks)}",
        f"{'━'*28}",
        f"\n⚠️ Bahis tavsiyesi değildir. Sorumluluk size aittir.",
    ])

    return "\n".join(lines)


# ─────────────────────────────────────
# DAILY SUMMARY (legacy — kept for free channel)
# ─────────────────────────────────────

def format_daily_summary(predictions: list, accuracy_stats: dict) -> str:
    """Daily channel post format (legacy confidence-centric)."""
    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    overall_acc = accuracy_stats.get("overall", 0)
    last7_acc = accuracy_stats.get("last_7_days", 0)

    lines = [
        f"🗓 {today} — Günün Tahminleri",
        f"{'━'*30}",
        f"📊 Son 7 Gün: %{last7_acc:.0f} {progress_bar(last7_acc)}",
        f"🏆 Genel: %{overall_acc:.0f} doğruluk",
        f"{'─'*30}",
    ]

    top = sorted(predictions, key=lambda x: x.get("confidence", 0), reverse=True)
    for pred in top[:5]:
        home = pred.get("home_team", "?")
        away = pred.get("away_team", "?")
        result = pred.get("predicted_result", "?")
        conf = pred.get("confidence", 0)
        ce = confidence_emoji(conf)
        re = result_emoji(result)

        lines.append(f"\n{ce} {home} vs {away}")
        lines.append(f"   {re} → %{conf:.0f} güven")

    if len(predictions) > 5:
        lines.append(f"\n... ve {len(predictions)-5} maç daha")

    lines.extend([
        f"\n{'━'*30}",
        "🤖 Güzel Tahmin | Premium",
    ])

    return "\n".join(lines)


# ─────────────────────────────────────
# RESULTS
# ─────────────────────────────────────

def format_results_post(results: list, date_str: str) -> str:
    """Yesterday's results — clean, scannable."""
    if not results:
        return ""

    correct = sum(1 for r in results if r.get("predicted_result") == r.get("actual_result"))
    total = len(results)
    acc = round(correct / total * 100, 1) if total else 0

    lines = [
        f"📋 SONUÇLAR — {date_str}",
        f"{'━' * 30}",
        f"🎯 Doğruluk: %{acc} ({correct}/{total})",
        f"{'─' * 30}",
    ]

    for r in results:
        hit = r.get("predicted_result") == r.get("actual_result")
        icon = "✅" if hit else "❌"
        lines.append(
            f"{icon} {r.get('home_team', '?')} vs {r.get('away_team', '?')}: "
            f"{r.get('predicted_result', '?')}→{r.get('actual_result', '?')}"
        )

    return "\n".join(lines)


# ─────────────────────────────────────
# ACCURACY REPORT
# ─────────────────────────────────────

def format_accuracy_report(stats: dict) -> str:
    """Historical accuracy report."""
    lines = [
        "📊 DOĞRULUK RAPORU",
        f"{'━'*30}",
    ]

    periods = [
        ("Son 7 Gün", stats.get("last_7_days", 0), stats.get("last_7_total", 0)),
        ("Son 30 Gün", stats.get("last_30_days", 0), stats.get("last_30_total", 0)),
        ("Tüm Zamanlar", stats.get("overall", 0), stats.get("total_predictions", 0)),
    ]

    for label, acc, total in periods:
        lines.append(f"\n{label}:")
        lines.append(f"  {progress_bar(acc)} %{acc:.1f} ({total} tahmin)")

    if stats.get("per_league"):
        lines.append(f"\n{'─'*30}")
        lines.append("📍 Lig Bazında:")
        for league, lacc in sorted(stats["per_league"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {league}: {progress_bar(lacc)} %{lacc:.1f}")

    if stats.get("per_result"):
        lines.append(f"\n{'─'*30}")
        labels = {"H": "Ev Sahibi", "D": "Berabere", "A": "Deplasman"}
        for res, racc in stats["per_result"].items():
            lines.append(f"  {labels.get(res, res)}: {progress_bar(racc)} %{racc:.1f}")

    return "\n".join(lines)


# ─────────────────────────────────────
# ADMIN
# ─────────────────────────────────────

def format_admin_stats(stats: dict) -> str:
    """Admin dashboard format."""
    lines = [
        "🔧 ADMIN PANELİ",
        f"{'━'*30}",
        f"\n👥 Üyeler:",
        f"  Toplam: {stats.get('total_subscribers', 0)}",
        f"  Aktif: {stats.get('active', 0)}",
        f"  Premium: {stats.get('premium', 0)}",
        f"  VIP: {stats.get('vip', 0)}",
        f"\n📊 Sistem:",
        f"  Doğruluk: %{stats.get('accuracy', 0):.1f}",
        f"  Günlük Tahmin: {stats.get('daily_predictions', 0)}",
        f"  API Sağlığı: {stats.get('api_health', '✅')}",
    ]

    if stats.get("expiring_soon"):
        lines.append(f"\n⚠️ {len(stats['expiring_soon'])} üye yakında sona eriyor")

    return "\n".join(lines)


def format_match_analysis_card(pred: dict, is_free: bool = False, promo_footer: str = "") -> str:
    """Strict quant-fund style markdown template formatting for predictions.

    Follows:
    🏴󠁧󠁢󠁥󠁮󠁧󠁿 [League Code] | [Home] vs [Away]
    🎯 ANA TAHMİN: [Main Pick]
    🔥 Güven: %[Confidence] | Oran: [Market Odds] | Değer (EV): +%[EV]
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🤖 AI Analizi: Eksikler: [Key Missing] | Güç Kaybı: -%[Power Drop]
    📊 Piyasa Oranları: 1=[Odds] | X=[Odds] | 2=[Odds]
    🧠 Model İhtimali: 1=%[Prob_1] | X=%[Prob_X] | 2=%[Prob_2]
    🎯 xG Beklentisi: [Home xG] – [Away xG]
    ────────────────────────────
    💡 2. Güçlü Seçenek: [Second Best Pick] (Güven: %[Confidence] | Oran: [Odds])
    """
    league_code = pred.get("league_code", "??")
    home = pred.get("home_team", "?")
    away = pred.get("away_team", "?")
    result = pred.get("predicted_result", "?")
    conf = pred.get("confidence", 0) or pred.get("confidence_score", 0) or 0

    h_prob = pred.get("h_prob")
    if h_prob is None:
        h_prob = pred.get("home_win_prob", 0) or 0
    d_prob = pred.get("d_prob")
    if d_prob is None:
        d_prob = pred.get("draw_prob", 0) or 0
    a_prob = pred.get("a_prob")
    if a_prob is None:
        a_prob = pred.get("away_win_prob", 0) or 0

    odds = pred.get("_odds") or {}
    h_odds = odds.get("h") or odds.get("home_odds")
    d_odds = odds.get("d") or odds.get("draw_odds")
    a_odds = odds.get("a") or odds.get("away_odds")

    # Normalize result representation to H/D/A for dictionary lookups
    result_norm = result
    if result in ("MS 1", "1", "Ev Sahibi"):
        result_norm = "H"
    elif result in ("MS X", "X", "Berabere"):
        result_norm = "D"
    elif result in ("MS 2", "2", "Deplasman"):
        result_norm = "A"

    # Map main pick result label to standard Turkish bookmaker terminology
    result_label = {"H": "MS 1", "D": "MS X", "A": "MS 2"}.get(result_norm, result)

    main_odds_val = {"H": h_odds, "D": d_odds, "A": a_odds}.get(result_norm)
    main_prob_val = {"H": h_prob, "D": d_prob, "A": a_prob}.get(result_norm)

    # Implied odds fallback if market odds are None or 0
    if not main_odds_val or main_odds_val == 0:
        main_odds_val = round(1 / max(main_prob_val, 0.01) * 1.08, 2)

    # EV calculation
    ev = (main_prob_val * main_odds_val) - 1.0
    ev_pct = ev * 100
    ev_str = f"+%{ev_pct:.1f}" if ev_pct >= 0 else f"-%{abs(ev_pct):.1f}"

    # AI Analizi: injuries & power loss
    home_status = pred.get("home_status") or {}
    away_status = pred.get("away_status") or {}

    home_key = home_status.get("key_absences", [])
    away_key = away_status.get("key_absences", [])
    home_loss = home_status.get("power_loss_pct", 0.0) or 0.0
    away_loss = away_status.get("power_loss_pct", 0.0) or 0.0

    home_missing_str = ", ".join(home_key) if home_key else "Yok"
    away_missing_str = ", ".join(away_key) if away_key else "Yok"
    key_missing_str = f"Ev: {home_missing_str} | Dep: {away_missing_str}"
    power_drop_str = f"Ev: -%{home_loss:.1f} | Dep: -%{away_loss:.1f}"

    # xG expectations: from lambdas or features
    hl = pred.get("home_lambda")
    al = pred.get("away_lambda")
    if hl is None or al is None:
        features = pred.get("features") or {}
        hl = features.get("home_goals_scored_avg", 1.3) or 1.3
        al = features.get("away_goals_scored_avg", 1.1) or 1.1
    xg_str = f"{hl:.1f} – {al:.1f}"

    # 2. Güçlü Seçenek (Runner-up class, Double Chance, Over/Under, BTTS)
    candidates = []
    
    # Runner up 1x2 class
    probs_1x2 = [("H", h_prob), ("D", d_prob), ("A", a_prob)]
    probs_1x2.sort(key=lambda x: x[1], reverse=True)
    runner_up_1x2_code = probs_1x2[1][0]
    runner_up_1x2_prob = probs_1x2[1][1]
    runner_up_1x2_odds = {"H": h_odds, "D": d_odds, "A": a_odds}.get(runner_up_1x2_code)
    if not runner_up_1x2_odds or runner_up_1x2_odds == 0:
        runner_up_1x2_odds = round(1 / max(runner_up_1x2_prob, 0.01) * 1.08, 2)
    runner_up_label = {"H": "MS 1", "D": "MS X", "A": "MS 2"}.get(runner_up_1x2_code)
    candidates.append({
        "label": runner_up_label,
        "prob": runner_up_1x2_prob,
        "odds": runner_up_1x2_odds
    })

    # Double chance options
    if h_odds and d_odds:
        dc_1x_odds = round(1.0 / ((1.0/h_odds + 1.0/d_odds)) * 1.08, 2)
        candidates.append({
            "label": "1X Çifte Şans",
            "prob": h_prob + d_prob,
            "odds": dc_1x_odds
        })
    if a_odds and d_odds:
        dc_x2_odds = round(1.0 / ((1.0/a_odds + 1.0/d_odds)) * 1.08, 2)
        candidates.append({
            "label": "X2 Çifte Şans",
            "prob": a_prob + d_prob,
            "odds": dc_x2_odds
        })
    if h_odds and a_odds:
        dc_12_odds = round(1.0 / ((1.0/h_odds + 1.0/a_odds)) * 1.08, 2)
        candidates.append({
            "label": "12 Çifte Şans",
            "prob": h_prob + a_prob,
            "odds": dc_12_odds
        })

    # Over / Under 2.5
    o25_prob = pred.get("over25_prob") or 0.0
    o25_odds = odds.get("o25") or odds.get("over25_odds")
    if o25_prob > 0 and o25_odds:
        candidates.append({
            "label": "2.5 ÜST",
            "prob": o25_prob,
            "odds": o25_odds
        })
        u25_odds = odds.get("u25") or odds.get("under25_odds")
        if u25_odds:
            candidates.append({
                "label": "2.5 ALT",
                "prob": 1.0 - o25_prob,
                "odds": u25_odds
            })

    # BTTS
    btts_prob = pred.get("btts_prob") or 0.0
    btts_odds = odds.get("btts") or odds.get("btts_y")
    if btts_prob > 0:
        if btts_odds:
            candidates.append({
                "label": "KG VAR",
                "prob": btts_prob,
                "odds": btts_odds
            })
        btts_no_odds = odds.get("btts_no") or odds.get("btts_n") or 1.95
        candidates.append({
            "label": "KG YOK",
            "prob": 1.0 - btts_prob,
            "odds": btts_no_odds
        })

    # Sort valid candidates by probability descending
    valid_candidates = [c for c in candidates if c["label"] != result_label and c["odds"] is not None and c["odds"] > 1.0]
    valid_candidates.sort(key=lambda x: x["prob"], reverse=True)

    if valid_candidates:
        sec_best = valid_candidates[0]
        sec_label = sec_best["label"]
        sec_conf = sec_best["prob"] * 100
        sec_odds = sec_best["odds"]
    else:
        sec_label = "KG VAR"
        sec_conf = 50.0
        sec_odds = 1.75

    odds_1_str = _odds_display(h_odds)
    odds_x_str = _odds_display(d_odds)
    odds_2_str = _odds_display(a_odds)

    flag = LEAGUE_EMOJI.get(league_code, "⚽")

    disclaimer = (
        "⚠️ Yasal Uyarı: Bu analiz ve tahminler sadece istatistiksel verilere dayanmaktadır, kesin kazanç taahhüt etmez. "
        "Bahis oynamak risk içerir; kayıplarınızdan sistemimiz sorumlu tutulamaz. Bahis tavsiyesi değildir. 18 yaşından büyükler içindir."
    )

    lines = [
        f" {flag} {league_code} | {home} vs {away}",
        f"🎯 ANA TAHMİN: {result_label}",
        f"🔥 Güven: %{conf:.0f} | Oran: {main_odds_val:.2f} | Değer (EV): {ev_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 AI Analizi: Eksikler: {key_missing_str} | Güç Kaybı: {power_drop_str}",
        f"📊 Piyasa Oranları: 1={odds_1_str} | X={odds_x_str} | 2={odds_2_str}",
        f"🧠 Model İhtimali: 1=%{h_prob*100:.0f} | X=%{d_prob*100:.0f} | 2=%{a_prob*100:.0f}",
        f"🎯 xG Beklentisi: {xg_str}",
        f"────────────────────────────",
        f"💡 2. Güçlü Seçenek: {sec_label} (Güven: %{sec_conf:.0f} | Oran: {sec_odds:.2f})\n",
        disclaimer
    ]

    if is_free and promo_footer:
        lines.append(f"\n{promo_footer}")

    return "\n".join(lines)


def format_performance_report(daily: dict, weekly: dict) -> str:
    """Format daily/weekly accuracy and ROI summary for marketing."""
    lines = [
        "🔥 *AI TAHMİN MOTORU PERFORMANS RAPORU* 🔥",
        f"{'━'*36}",
        "",
        "📅 *DÜNÜN ÖZETİ (Yatırım Getirisi & Başarı)*",
        f"• Toplam Tahmin: *{daily['total']}*",
        f"• Başarılı Tahmin: *{daily['correct']}*",
        f"🎯 *İsabet Oranı:* `%{daily['accuracy']:.1f}`",
        f"💰 *Net Kâr/Zarar:* `{daily['profit']:+.2f} Birim`",
        f"📈 *ROI (Yatırım Getirisi):* `%{daily['roi']:.1f}`" if daily['staked'] > 0 else "📈 *ROI:* `%0.0`",
        "",
        "📅 *SON 7 GÜNÜN ÖZETİ*",
        f"• Toplam Tahmin: *{weekly['total']}*",
        f"• Başarılı Tahmin: *{weekly['correct']}*",
        f"🎯 *İsabet Oranı:* `%{weekly['accuracy']:.1f}`",
        f"💰 *Net Kâr/Zarar:* `{weekly['profit']:+.2f} Birim`",
        f"📈 *ROI (Yatırım Getirisi):* `%{weekly['roi']:.1f}`" if weekly['staked'] > 0 else "📈 *ROI:* `%0.0`",
        "",
        f"{'━'*36}",
        "🤖 *Güzel Tahmin Ensemble AI*",
        "Kasayı düzenli ve bilimsel büyütmek için VIP kanalımıza katılın! 👇",
        "🔗 VIP Üyelik & Detaylar için: @GüzelTahminBot"
    ]
    return "\n".join(lines)


