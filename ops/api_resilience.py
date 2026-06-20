"""
ops/api_resilience.py — Retry + Circuit Breaker

İki bileşen:
  resilient_get()   HTTP GET için yeniden deneme (exponential backoff).
  CircuitBreaker    Tekrarlayan hatalarda API'yi geçici olarak devre dışı bırakır.

Backend seçimi (otomatik):
  SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY env var varsa → Supabase kv_store
  Yoksa → data/cb_state.json (yerel geliştirme)

  GitHub Actions ephemeral runner sorunu: her run yeni bir VM açar ve
  yerel JSON kaybolur. Supabase backend kalıcı durum sağlar.

Kullanım:
    from ops.api_resilience import resilient_get, CircuitBreaker

    cb   = CircuitBreaker("odds_api")
    resp = resilient_get(url, params=params, circuit_breaker=cb)
    if resp is None:
        # circuit açık veya tüm denemeler başarısız — fallback uygula
        ...
"""
from __future__ import annotations

import json
import logging
import os
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

_LOCAL_STATE_FILE = Path("data/cb_state.json")
_FAILURE_THRESH   = 3       # art arda bu kadar hata → devre açılır
_RECOVERY_SEC     = 3600    # 1 saat sonra HALF_OPEN'a geç

_RETRIABLE_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
_PERMANENT_ERRORS:   frozenset[int] = frozenset({401, 403, 404, 422})

# Supabase kv_store anahtarı prefixiyle çakışma önlenir
_CB_KEY_PREFIX = "cb:"


# ---------------------------------------------------------------------------
# Storage Backend — Supabase veya yerel JSON
# ---------------------------------------------------------------------------

class _SupabaseBackend:
    """
    Circuit breaker durumunu Supabase kv_store tablosuna yazar/okur.
    Ephemeral GitHub Actions runner'lar arasında kalıcılık sağlar.
    """

    def __init__(self) -> None:
        self._url = os.environ["SUPABASE_URL"].rstrip("/")
        self._key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self._headers = {
            "apikey":        self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }

    def load(self, name: str) -> dict:
        key = f"{_CB_KEY_PREFIX}{name}"
        try:
            resp = requests.get(
                f"{self._url}/rest/v1/kv_store",
                headers=self._headers,
                params={"key": f"eq.{key}", "select": "value"},
                timeout=5,
            )
            if resp.status_code == 200:
                rows = resp.json()
                if rows:
                    return rows[0]["value"]
        except Exception as exc:
            logger.warning("[CB:%s] Supabase okuma hatası: %s", name, exc)
        return _default_cb_state()

    def save(self, name: str, state: dict) -> None:
        key = f"{_CB_KEY_PREFIX}{name}"
        payload = {"key": key, "value": state}
        try:
            resp = requests.post(
                f"{self._url}/rest/v1/kv_store",
                headers={**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=payload,
                timeout=5,
            )
            if resp.status_code not in (200, 201, 204):
                logger.warning("[CB:%s] Supabase yazma hatası: HTTP %d %s",
                               name, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[CB:%s] Supabase yazma exception: %s", name, exc)


class _FileBackend:
    """Yerel JSON dosyası — geliştirme ortamı için."""

    def load(self, name: str) -> dict:
        try:
            if _LOCAL_STATE_FILE.exists():
                return json.loads(_LOCAL_STATE_FILE.read_text()).get(name, _default_cb_state())
        except Exception:
            pass
        return _default_cb_state()

    def save(self, name: str, state: dict) -> None:
        try:
            _LOCAL_STATE_FILE.parent.mkdir(exist_ok=True)
            all_states: dict = {}
            if _LOCAL_STATE_FILE.exists():
                try:
                    all_states = json.loads(_LOCAL_STATE_FILE.read_text())
                except Exception:
                    pass
            all_states[name] = state
            _LOCAL_STATE_FILE.write_text(json.dumps(all_states, indent=2))
        except Exception as exc:
            logger.warning("[CB:%s] Dosya yazma hatası: %s", name, exc)


def _make_backend() -> _SupabaseBackend | _FileBackend:
    """Env var'a göre backend seç. CI'da Supabase, lokalde dosya."""
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        logger.debug("[CB] Backend: Supabase kv_store")
        return _SupabaseBackend()
    logger.debug("[CB] Backend: yerel JSON (%s)", _LOCAL_STATE_FILE)
    return _FileBackend()


def _default_cb_state() -> dict:
    return {
        "state":           "CLOSED",
        "failure_count":   0,
        "last_failure_at": None,
        "open_until":      None,
    }


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base: float = 5.0
    backoff_factor: float = 5.0     # 5s → 25s → 125s
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
      requests.Response  — başarılı yanıt (2xx dahil kalıcı 4xx)
      None               — devre açık veya tüm denemeler başarısız
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

            if resp.status_code in _PERMANENT_ERRORS:
                logger.warning("[Retry] HTTP %d kalıcı hata — deneme durduruluyor.", resp.status_code)
                if circuit_breaker:
                    circuit_breaker.record_failure()
                return resp

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
            logger.error("[Retry] Beklenmeyen hata: %s", exc)
            if circuit_breaker:
                circuit_breaker.record_failure()
            raise

    logger.error("[Retry] %d denemede başarıya ulaşılamadı. Son hata: %s",
                 retry.max_attempts, last_exc)
    if circuit_breaker:
        circuit_breaker.record_failure()
    return None


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Durum makinesi: CLOSED → OPEN → HALF_OPEN → CLOSED

    Backend otomatik seçilir:
      - SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY varsa → Supabase kv_store
        (GitHub Actions ephemeral runner'lar arasında kalıcı)
      - Yoksa → data/cb_state.json (yerel geliştirme)

    Geçişler:
      CLOSED    → OPEN       : art arda failure_threshold hata
      OPEN      → HALF_OPEN  : recovery_sec geçince
      HALF_OPEN → CLOSED     : test isteği başarılı
      HALF_OPEN → OPEN       : test isteği başarısız
    """

    def __init__(self, name: str, *,
                 failure_threshold: int = _FAILURE_THRESH,
                 recovery_sec: int = _RECOVERY_SEC):
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_sec      = recovery_sec
        self._backend          = _make_backend()
        self._state            = self._backend.load(name)

    # ── public API ──────────────────────────────────────────────────────────

    def allow_request(self) -> bool:
        s = self._state
        if s["state"] == "CLOSED":
            return True
        if s["state"] == "OPEN":
            if time.time() >= (s.get("open_until") or 0):
                self._transition("HALF_OPEN")
                return True
            return False
        # HALF_OPEN: tek test isteği geçer
        return True

    def record_success(self) -> None:
        s = self._state
        if s["state"] in ("HALF_OPEN", "OPEN"):
            logger.info("[CB:%s] Başarılı yanıt — devre kapatıldı (CLOSED).", self.name)
        s["state"]         = "CLOSED"
        s["failure_count"] = 0
        s["open_until"]    = None
        self._backend.save(self.name, s)

    def record_failure(self) -> None:
        s = self._state
        s["failure_count"]   = s.get("failure_count", 0) + 1
        s["last_failure_at"] = time.time()

        if s["state"] == "HALF_OPEN" or s["failure_count"] >= self.failure_threshold:
            open_until  = time.time() + self.recovery_sec
            s["state"]      = "OPEN"
            s["open_until"] = open_until
            logger.warning(
                "[CB:%s] Devre AÇILDI — %d art arda hata. %s'e kadar askıya alındı.",
                self.name, s["failure_count"],
                datetime.fromtimestamp(open_until, tz=timezone.utc).strftime("%H:%M UTC"),
            )
        self._backend.save(self.name, s)

    def open_until_human(self) -> str:
        ts = self._state.get("open_until")
        if not ts:
            return "—"
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def status(self) -> dict:
        return dict(self._state)

    # ── private ─────────────────────────────────────────────────────────────

    def _transition(self, new_state: str) -> None:
        old = self._state["state"]
        self._state["state"] = new_state
        logger.info("[CB:%s] %s → %s", self.name, old, new_state)
        self._backend.save(self.name, self._state)
