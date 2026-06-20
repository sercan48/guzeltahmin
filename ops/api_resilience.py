"""
ops/api_resilience.py — Retry + Circuit Breaker

İki bileşen:
  resilient_get()   HTTP GET için yeniden deneme (exponential backoff).
  CircuitBreaker    Tekrarlayan hatalarda API'yi geçici olarak devre dışı bırakır.

Kullanım:
    from ops.api_resilience import resilient_get, CircuitBreaker

    cb   = CircuitBreaker("odds_api")
    resp = resilient_get(url, params=params, circuit_breaker=cb)
    if resp is None:
        # circuit açık veya tüm denemeler başarısız
        ...

Devam durumu (GH Actions ortamı):
  data/cb_state.json dosyası settle adımında git'e commit edilir.
  Bu sayede circuit breaker durumu iş akışları arasında korunur.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

_CB_STATE_FILE   = Path("data/cb_state.json")
_FAILURE_THRESH  = 3       # art arda bu kadar hata → devre açılır
_RECOVERY_SEC    = 3600    # 1 saat sonra HALF_OPEN'a geç

# HTTP durum kodları: geçici (yeniden denenebilir) vs kalıcı (deneme)
_RETRIABLE_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
_PERMANENT_ERRORS:   frozenset[int] = frozenset({401, 403, 404, 422})


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base: float = 5.0        # saniye; 5 → 25 → 125
    backoff_factor: float = 5.0      # her denemede çarpan
    retriable_statuses: Sequence[int] = field(
        default_factory=lambda: list(_RETRIABLE_STATUSES)
    )


def resilient_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
    retry: RetryConfig | None = None,
    circuit_breaker: "CircuitBreaker | None" = None,
) -> requests.Response | None:
    """
    requests.get() yerine kullan. Retry ve circuit breaker desteği var.

    Dönüş:
      requests.Response  — başarılı yanıt (status 200-299)
      None               — devre açık veya tüm denemeler başarısız

    Caller 2xx dışı durumları kendi ele alır; bu fonksiyon sadece
    bağlantı sorunlarını (timeout, 5xx) tekrarlar.
    """
    if retry is None:
        retry = RetryConfig()

    if circuit_breaker and not circuit_breaker.allow_request():
        logger.warning(
            "[CB:%s] Devre açık — istek atlandı. Yeniden denenecek: %s",
            circuit_breaker.name,
            circuit_breaker.open_until_human(),
        )
        return None

    last_exc: Exception | None = None

    for attempt in range(1, retry.max_attempts + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)

            # Kalıcı hata → tekrar deneme faydasız
            if resp.status_code in _PERMANENT_ERRORS:
                logger.warning("[Retry] HTTP %d kalıcı hata — deneme durduruluyor.", resp.status_code)
                if circuit_breaker:
                    circuit_breaker.record_failure()
                return resp

            # Geçici hata → bekle ve tekrar dene
            if resp.status_code in retry.retriable_statuses:
                wait = retry.backoff_base * (retry.backoff_factor ** (attempt - 1))
                logger.warning(
                    "[Retry] HTTP %d — deneme %d/%d, %.0fs beklenecek.",
                    resp.status_code, attempt, retry.max_attempts, wait,
                )
                if attempt < retry.max_attempts:
                    time.sleep(wait)
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                continue

            # Başarı
            if circuit_breaker:
                circuit_breaker.record_success()
            return resp

        except (requests.Timeout, requests.ConnectionError) as exc:
            wait = retry.backoff_base * (retry.backoff_factor ** (attempt - 1))
            logger.warning(
                "[Retry] %s — deneme %d/%d, %.0fs beklenecek: %s",
                type(exc).__name__, attempt, retry.max_attempts, wait, exc,
            )
            if attempt < retry.max_attempts:
                time.sleep(wait)
            last_exc = exc

        except Exception as exc:
            # Beklenmeyen hata → tekrar deneme yok
            logger.error("[Retry] Beklenmeyen hata: %s", exc)
            if circuit_breaker:
                circuit_breaker.record_failure()
            raise

    # Tüm denemeler tükendi
    logger.error("[Retry] %d denemede başarıya ulaşılamadı. Son hata: %s", retry.max_attempts, last_exc)
    if circuit_breaker:
        circuit_breaker.record_failure()
    return None


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Dosya tabanlı (data/cb_state.json) durum makinesi.

    Durumlar:
      CLOSED    → normal, istekler geçer
      OPEN      → hata eşiği aşıldı, istekler bloke
      HALF_OPEN → _RECOVERY_SEC sonra test isteği gönderilir

    Geçişler:
      CLOSED → OPEN      : art arda _FAILURE_THRESH hata
      OPEN → HALF_OPEN   : _RECOVERY_SEC geçince
      HALF_OPEN → CLOSED : test başarılı
      HALF_OPEN → OPEN   : test başarısız
    """

    def __init__(self, name: str, *, failure_threshold: int = _FAILURE_THRESH,
                 recovery_sec: int = _RECOVERY_SEC):
        self.name             = name
        self.failure_threshold = failure_threshold
        self.recovery_sec     = recovery_sec
        self._state           = self._load()

    # ── public API ──────────────────────────────────────────────────────────

    def allow_request(self) -> bool:
        s = self._state
        if s["state"] == "CLOSED":
            return True
        if s["state"] == "OPEN":
            if self._now() >= s.get("open_until", 0):
                self._transition("HALF_OPEN")
                return True
            return False
        if s["state"] == "HALF_OPEN":
            return True
        return True

    def record_success(self) -> None:
        s = self._state
        if s["state"] in ("HALF_OPEN", "OPEN"):
            logger.info("[CB:%s] Başarılı yanıt — devre kapatıldı (CLOSED).", self.name)
        s["state"]         = "CLOSED"
        s["failure_count"] = 0
        s["open_until"]    = None
        self._save()

    def record_failure(self) -> None:
        s = self._state
        s["failure_count"] = s.get("failure_count", 0) + 1
        s["last_failure_at"] = self._now()

        if s["state"] == "HALF_OPEN" or s["failure_count"] >= self.failure_threshold:
            open_until = self._now() + self.recovery_sec
            s["state"]      = "OPEN"
            s["open_until"] = open_until
            logger.warning(
                "[CB:%s] Devre AÇILDI — %d art arda hata. %s'e kadar askıya alındı.",
                self.name, s["failure_count"],
                datetime.fromtimestamp(open_until, tz=timezone.utc).strftime("%H:%M UTC"),
            )
        self._save()

    def open_until_human(self) -> str:
        ts = self._state.get("open_until")
        if not ts:
            return "—"
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def status(self) -> dict:
        return dict(self._state)

    # ── private ─────────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> float:
        return time.time()

    def _transition(self, new_state: str) -> None:
        old = self._state["state"]
        self._state["state"] = new_state
        logger.info("[CB:%s] %s → %s", self.name, old, new_state)
        self._save()

    def _load(self) -> dict:
        try:
            if _CB_STATE_FILE.exists():
                all_states = json.loads(_CB_STATE_FILE.read_text())
                return all_states.get(self.name, self._default_state())
        except Exception:
            pass
        return self._default_state()

    def _save(self) -> None:
        try:
            _CB_STATE_FILE.parent.mkdir(exist_ok=True)
            all_states: dict = {}
            if _CB_STATE_FILE.exists():
                try:
                    all_states = json.loads(_CB_STATE_FILE.read_text())
                except Exception:
                    pass
            all_states[self.name] = self._state
            _CB_STATE_FILE.write_text(json.dumps(all_states, indent=2))
        except Exception as e:
            logger.warning("[CB:%s] Durum kaydedilemedi: %s", self.name, e)

    @staticmethod
    def _default_state() -> dict:
        return {
            "state":           "CLOSED",
            "failure_count":   0,
            "last_failure_at": None,
            "open_until":      None,
        }
