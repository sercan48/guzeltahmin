"""PHASE-LIVE L6 — Provider runtime validation (startup pre-flight checks).

Verifies, before the service loop starts:
  1. Required credentials are present (via SecretProvider.has)
  2. Provider endpoints are reachable (optional, via injected probe)
  3. Provider health can be bootstrapped

All checks are offline-capable: the reachability probe is injectable so tests
never touch the network. In production, pass a probe backed by UrllibHttpClient.

No secrets are ever logged or returned — only presence booleans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from ..activation.transport import SecretProvider


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: str          # CheckStatus value
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class PreflightReport:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.status != CheckStatus.FAIL.value for r in self.results)

    @property
    def n_pass(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.PASS.value)

    @property
    def n_fail(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.FAIL.value)

    @property
    def n_skip(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.SKIP.value)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "n_skip": self.n_skip,
            "results": [r.to_dict() for r in self.results],
        }


# Reachability probe signature: (endpoint_name) -> (ok: bool, detail: str)
ReachabilityProbe = Callable[[str], tuple]


class ProviderValidator:
    """Runs startup verification for provider credentials and reachability."""

    def __init__(
        self,
        secret_provider: SecretProvider,
        reachability_probe: Optional[ReachabilityProbe] = None,
    ) -> None:
        self._secrets = secret_provider
        self._probe = reachability_probe

    # ------------------------------------------------------------------ #

    def check_credentials(self, required_secrets: List[str]) -> List[CheckResult]:
        """Verify each required secret is present. Never reads the value."""
        results: List[CheckResult] = []
        for name in required_secrets:
            present = False
            try:
                present = self._secrets.has(name)
            except Exception as exc:  # defensive: has() should not raise
                results.append(CheckResult(
                    f"credential:{name}", CheckStatus.FAIL.value,
                    f"error checking secret: {type(exc).__name__}",
                ))
                continue
            if present:
                results.append(CheckResult(
                    f"credential:{name}", CheckStatus.PASS.value, "present"
                ))
            else:
                results.append(CheckResult(
                    f"credential:{name}", CheckStatus.FAIL.value, "missing"
                ))
        return results

    def check_reachability(self, endpoints: List[str]) -> List[CheckResult]:
        """Probe each endpoint. SKIP if no probe injected (offline mode)."""
        results: List[CheckResult] = []
        for ep in endpoints:
            if self._probe is None:
                results.append(CheckResult(
                    f"reachability:{ep}", CheckStatus.SKIP.value,
                    "no probe configured (offline)",
                ))
                continue
            try:
                ok, detail = self._probe(ep)
                status = CheckStatus.PASS.value if ok else CheckStatus.FAIL.value
                results.append(CheckResult(f"reachability:{ep}", status, detail))
            except Exception as exc:
                results.append(CheckResult(
                    f"reachability:{ep}", CheckStatus.FAIL.value,
                    f"probe error: {type(exc).__name__}",
                ))
        return results

    def run(
        self,
        required_secrets: List[str],
        endpoints: Optional[List[str]] = None,
    ) -> PreflightReport:
        """Run full pre-flight: credentials + reachability."""
        report = PreflightReport()
        report.results.extend(self.check_credentials(required_secrets))
        report.results.extend(self.check_reachability(endpoints or []))
        return report
