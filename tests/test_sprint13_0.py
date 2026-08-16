"""Sprint 13.0 — Production readiness gate infrastructure.

Covers the *testable* surface of ``benchmarks.production_gate`` (the
consolidated production-readiness command) plus the two production
fixes this sprint shipped:

- **Gate core** (pure helpers, no servers): section status semantics
  (``PASS``/``FAIL``/``SKIPPED``/``CONDITIONAL``), the reviewer-confirmed
  evidence-loss fix (``pass_`` must *merge* details, never replace),
  ``compute_verdict`` (FAIL -> NOT PRODUCTION READY; CONDITIONAL ->
  CONDITIONALLY PRODUCTION READY; else PRODUCTION READY), state
  snapshot/verification, and the hermetic provider-env override.
- **Security audit helper**: ``audit_source_scan`` must flag unsafe
  ``pickle.load`` / ``eval`` / ``exec`` / hardcoded secrets in a
  synthetic tree and must be clean against the *current* ``src/``.
- **Unsafe-deserialisation fix** (P0 finding): the model manager's
  restricted unpickler round-trips a legitimate model artifact, blocks
  a gadget-pickle (``builtins.eval``), and the path-containment guard
  refuses an artifact outside the manager's directory.

Server-spawning sections (api_live, cache, multi_worker, concurrency,
streamlit, recovery, security-live, startup_deploy, stability) are
exercised by ``python -m benchmarks.production_gate`` itself — the
sprint's Phase 16 consolidated command — not duplicated here.  These
tests never spawn a server and never touch production state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.production_gate import (
    CONDITIONAL,
    FAIL,
    PASS,
    SKIPPED,
    Section,
    _close_section,
    _offline_provider_env,
    audit_source_scan,
    compute_verdict,
    snapshot_state,
    verify_state_unchanged,
)


# ───────────────────────────────────────────────────────────────────
# Section semantics + evidence preservation (reviewer-fix regression)
# ───────────────────────────────────────────────────────────────────


class TestSectionSemantics:
    def test_pass_merges_details_instead_of_replacing(self):
        """Sections accumulate measurements into ``details`` before
        ``pass_()`` — a replace semantics wiped every measurement from
        the artifact (``core_pipeline PASS {}``, reviewer-confirmed).
        """
        sec = Section("demo")
        sec.details["synthetic_symbols"] = 12
        sec.details["synthetic_analysed"] = 12
        sec.pass_()
        assert sec.status == PASS
        assert sec.details["synthetic_symbols"] == 12
        assert sec.details["synthetic_analysed"] == 12

    def test_pass_with_kwargs_merges_with_prior_details(self):
        sec = Section("demo")
        sec.details["before"] = 1
        sec.pass_(after=2)
        assert sec.details == {"before": 1, "after": 2}

    def test_fail_replaces_with_reason(self):
        sec = Section("demo")
        sec.details["measurement"] = 99
        sec.fail("boom", measurement=99)
        assert sec.status == FAIL
        assert sec.details["reason"] == "boom"

    def test_skip_and_conditional(self):
        sec = Section("a").skip("no docker")
        assert sec.status == SKIPPED
        assert sec.details["reason"] == "no docker"
        sec = Section("b").conditional("memory grew", delta=12.0)
        assert sec.status == CONDITIONAL
        assert sec.details["delta"] == 12.0

    def test_to_dict_round_trip(self):
        sec = Section("demo").note("hello").pass_(x=1)
        d = sec.to_dict()
        assert d["name"] == "demo"
        assert d["status"] == PASS
        assert d["notes"] == ["hello"]
        assert d["details"] == {"x": 1}


# ───────────────────────────────────────────────────────────────────
# Verdict computation
# ───────────────────────────────────────────────────────────────────


class TestComputeVerdict:
    def test_no_fail_no_conditional_is_production_ready(self):
        sections = [
            Section("a").pass_(),
            Section("b").skip("optional"),
        ]
        assert compute_verdict(sections) == "PRODUCTION READY"

    def test_any_fail_is_not_production_ready(self):
        sections = [
            Section("a").pass_(),
            Section("b").fail("broken"),
            Section("c").conditional("note"),
        ]
        assert compute_verdict(sections) == "NOT PRODUCTION READY"

    def test_conditional_without_fail_is_conditional(self):
        sections = [
            Section("a").pass_(),
            Section("b").conditional("docker not available"),
        ]
        assert compute_verdict(sections) == "CONDITIONALLY PRODUCTION READY"

    def test_skipped_does_not_block_production_ready(self):
        sections = [Section("a").pass_(), Section("b").skip("no docker")]
        assert compute_verdict(sections) == "PRODUCTION READY"


# ───────────────────────────────────────────────────────────────────
# State isolation snapshot helpers
# ───────────────────────────────────────────────────────────────────


class TestStateSnapshot:
    def test_snapshot_unchanged_passes(self, tmp_path):
        f = tmp_path / "data" / "alerts"
        f.mkdir(parents=True)
        (f / "history.json").write_text("{}", encoding="utf-8")
        before = snapshot_state(tmp_path)
        after = snapshot_state(tmp_path)
        assert verify_state_unchanged(before, after) is True

    def test_snapshot_detects_content_change(self, tmp_path):
        f = tmp_path / "data" / "alerts"
        f.mkdir(parents=True)
        target = f / "history.json"
        target.write_text("{}", encoding="utf-8")
        before = snapshot_state(tmp_path)
        target.write_text('{"x": 1}', encoding="utf-8")
        after = snapshot_state(tmp_path)
        assert verify_state_unchanged(before, after) is False

    def test_snapshot_tolerates_missing_files(self, tmp_path):
        before = snapshot_state(tmp_path)
        assert verify_state_unchanged(before, before) is True
        assert all(v["size"] == -1 for v in before.values())


# ───────────────────────────────────────────────────────────────────
# Hermetic provider env (offline gate determinism)
# ───────────────────────────────────────────────────────────────────


class TestOfflineProviderEnv:
    def test_all_provider_urls_point_at_dead_loopback(self):
        env = _offline_provider_env()
        for key in ("NEPSE_SCRAPER_URL", "NEPSE_CLIENT_URL", "GITHUB_DATASETS_URL"):
            assert env[key] == "http://127.0.0.1:1", key
        assert env["NEPSE_DATA_API_URL"] == ""
        assert env["NEPALSTOCK_OFFICIAL_URL"] == ""

    def test_env_keys_are_config_recognised(self):
        """Every override key must be one the config actually reads (via
        ``os.getenv`` inside ``DATA_SERVICE_API_URLS``), so the hermetic
        override is effective — not dead config."""
        from src import config

        source = Path(config.__file__).read_text(encoding="utf-8")
        for key in _offline_provider_env():
            # The key must appear in the config source (inside an
            # ``os.getenv`` call — the call and its key may be on
            # separate lines, so match the quoted key itself).
            assert f'"{key}"' in source, key


# ───────────────────────────────────────────────────────────────────
# Security: static audit helper
# ───────────────────────────────────────────────────────────────────


class TestAuditSourceScan:
    def test_flags_unsafe_deserialisation(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text(
            "import pickle\n"
            "def load(path):\n"
            "    with open(path, 'rb') as f:\n"
            "        return pickle.load(f)\n",
            encoding="utf-8",
        )
        hits = audit_source_scan(tmp_path)
        assert hits["pickle_load"], "pickle.load must be flagged"
        assert not hits["eval_exec"]
        assert not hits["hardcoded_secret"]

    def test_flags_eval_and_exec(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("x = eval('1+1')\ny = exec('pass')\n", encoding="utf-8")
        hits = audit_source_scan(tmp_path)
        assert hits["eval_exec"], "eval/exec must be flagged"

    def test_flags_hardcoded_secrets(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("TELEGRAM_TOKEN = '123:secret'\n", encoding="utf-8")
        hits = audit_source_scan(tmp_path)
        assert hits["hardcoded_secret"], "hardcoded token must be flagged"

    def test_current_src_tree_is_clean(self):
        """The security section of the gate depends on this: no unsafe
        deserialisation, no eval/exec, no hardcoded secrets in ``src``.
        (The Sprint 13 restricted-unpickler fix removed the last
        ``pickle.load`` in ``src/ml/model_manager.py``.)"""
        from benchmarks import production_gate

        hits = audit_source_scan(production_gate.PROJECT_ROOT / "src")
        assert not hits["pickle_load"], hits["pickle_load"][:3]
        assert not hits["eval_exec"], hits["eval_exec"][:3]
        assert not hits["hardcoded_secret"], hits["hardcoded_secret"][:3]


# ───────────────────────────────────────────────────────────────────
# Security: restricted unpickler fix (src/ml/model_manager.py)
# ───────────────────────────────────────────────────────────────────


class TestRestrictedModelLoading:
    def test_legitimate_model_round_trips(self, tmp_path):
        """A pickled dict model round-trips through the restricted loader."""
        import pickle

        from src.ml import model_manager as mm

        artifact = tmp_path / "model.pkl"
        model = {"features": ["close", "volume"], "threshold": 0.5}
        with artifact.open("wb") as f:
            pickle.dump(model, f)

        result = mm._load_artifact_restricted(artifact)
        assert result == model

    def test_gadget_pickle_is_blocked(self, tmp_path):
        """A pickle that smuggles ``builtins.eval`` (the classic RCE
        gadget) must raise — the restricted unpickler allowlists safe
        builtins only."""
        import pickle

        from src.ml import model_manager as mm

        gadget = tmp_path / "gadget.pkl"

        class _Evil:
            def __reduce__(self):
                return (eval, ("__import__('os').getcwd()",))

        with gadget.open("wb") as f:
            pickle.dump(_Evil(), f)

        with pytest.raises(Exception):
            mm._load_artifact_restricted(gadget)

    def test_load_rejects_artifact_outside_model_dir(self, tmp_path):
        """Path containment: ``ModelManager.load`` must refuse a version
        record whose artifact path is outside the manager directory — a
        tampered record cannot point the loader at an arbitrary file."""
        import pickle

        from src.ml.model_manager import ModelManager
        from src.ml.versioning import ModelVersion

        manager = ModelManager(tmp_path / "models")
        outside = tmp_path / "outside.pkl"
        with outside.open("wb") as f:
            pickle.dump({"x": 1}, f)

        # Seed a version record whose ``path`` points outside the
        # manager directory (tampered-record simulation).
        manager._versions.save(
            ModelVersion(model_name="evil", version="1.0.0", path=str(outside))
        )
        assert manager.load("evil") is None

    def test_allowlisted_module_families_load(self, tmp_path):
        """The allowlist covers the sklearn/numpy families — a small
        sklearn estimator must survive the restricted loader."""
        import pickle

        pytest.importorskip("sklearn")
        from sklearn.linear_model import LinearRegression

        from src.ml import model_manager as mm

        artifact = tmp_path / "sk.pkl"
        with artifact.open("wb") as f:
            pickle.dump(LinearRegression(), f)

        loaded = mm._load_artifact_restricted(artifact)
        assert loaded is not None
        assert hasattr(loaded, "predict")


# ───────────────────────────────────────────────────────────────────
# Per-section server teardown (Sprint 13 multi_worker pollution fix)
# ───────────────────────────────────────────────────────────────────


class TestCloseSection:
    """``_close_section`` tears down only the calling section's own
    server from the shared pool, so later sections measure on a quiet
    machine instead of under 3+ co-resident uvicorn processes (the
    in-gate multi_worker failure: 4/8 requests timed out at 60 s under
    pollution, while the same section passed 8/8 standalone)."""

    class _FakePool:
        def __init__(self) -> None:
            self.popped: list[int] = []
            self.shutdown_all_calls = 0

        def pop(self, port: int) -> None:
            self.popped.append(port)

        def shutdown_all(self) -> None:
            self.shutdown_all_calls += 1

    def test_pops_own_port_in_shared_mode(self):
        """Shared pool (owned=False): pop the section's own port, never
        shutdown the shared pool."""
        pool = self._FakePool()
        _close_section(pool, owned=False, port=8914)
        assert pool.popped == [8914]
        assert pool.shutdown_all_calls == 0

    def test_pops_own_port_and_owns_pool_in_standalone_mode(self):
        """Standalone (owned=True): pop the own server AND shutdown the
        pool so a direct call never leaks a uvicorn process."""
        pool = self._FakePool()
        _close_section(pool, owned=True, port=8901)
        assert pool.popped == [8901]
        assert pool.shutdown_all_calls == 1

    def test_preserves_other_sections_when_own_port_absent(self):
        """The calling contract: ``_close_section`` always pops its own
        port (the real ``ServerPool.pop`` is itself a no-op when the
        port was never spawned — that behaviour lives in the pool and is
        exercised by the recovery section in the gate).  A failed-before-
        spawn section must not touch another section's server."""
        pool = self._FakePool()
        pool.popped.append(8800)  # another section's server
        _close_section(pool, owned=False, port=8914)
        assert pool.popped == [8800, 8914]

    def test_none_pool_is_safe(self):
        _close_section(None, owned=True, port=8914)  # no error

    def test_real_pool_pop_noop_when_port_absent(self):
        """The real ``ServerPool.pop`` is a no-op for a port that was
        never spawned (covers the docstring's 'failed before spawning'
        claim directly on the production helper, not a fake)."""
        from benchmarks.production_gate import ServerPool

        pool = ServerPool()
        pool.pop(99999)  # no error, nothing shut down
        assert pool._ports == []
        assert pool._servers == []


# ───────────────────────────────────────────────────────────────────
# Gate artifact structure (last committed run, if present)
# ───────────────────────────────────────────────────────────────────


class TestGateArtifact:
    def test_committed_artifact_has_verdict_and_sections(self):
        """If a gate artifact exists under benchmarks/results, it must
        carry the full report shape — verdict, state isolation, section
        statuses.  (The gate writes this on every run; the committed
        copy documents the last verified state.)"""
        from benchmarks import production_gate

        artifact_path = (
            production_gate.PROJECT_ROOT / "benchmarks" / "results" / "production_gate.json"
        )
        if not artifact_path.exists():
            pytest.skip("no committed gate artifact yet")
        report = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert report["verdict"] in (
            "PRODUCTION READY",
            "CONDITIONALLY PRODUCTION READY",
            "NOT PRODUCTION READY",
        )
        assert report["state_isolation"] == "PASS"
        assert report["sections"]
        assert report["summary"]["pass"] + report["summary"]["fail"] \
            + report["summary"]["conditional"] + report["summary"]["skipped"] \
            == len(report["sections"])
        for sec in report["sections"]:
            assert sec["status"] in (PASS, FAIL, SKIPPED, CONDITIONAL)


# ───────────────────────────────────────────────────────────────────
# BOT section — contract validator + script template + /analyze fix
# ───────────────────────────────────────────────────────────────────


def _well_formed_bot_results() -> dict:
    """A realistic bot-check payload (all 12 handlers registered + replied)."""
    help_text = (
        "📈 NEPSE Quant Bot Online\n\nAvailable Commands:\n"
        "/analyze SYMBOL\n/backtest SYMBOL\n/signals SYMBOL\n/top10\n"
        "/buylist\n/selllist\n/strongbuy\n/market\n/watchlist\n"
        "/watchlist scan\n/portfolio\n/help"
    )
    return {
        "registered": [
            "analyze", "backtest", "buylist", "help", "market",
            "portfolio", "selllist", "signals", "start", "strongbuy",
            "top10", "watchlist",
        ],
        "token": "dummy",
        "start": {"replies": [help_text], "error": None},
        "help": {"replies": [help_text], "error": None},
        "analyze": {
            "replies": [
                "📊 SYN000\n\n🟢 Signal: BUY\n🎯 Confidence: 71%\n\n"
                "💰 Current Price\nRs. 100.00\n\n🛒 Buy Zone\n99.00 - 101.00\n\n"
                "🛑 Stop Loss\nRs. 95.00\n\n🎯 Target 1\nRs. 105.00\n\n"
                "📊 Score : 6\n📉 RSI : 55.20\n📈 MACD : 0.0012"
            ],
            "error": None,
        },
        "top10": {"replies": ["🏆 Top 10 Stocks\n\n1. SYN000 | BUY | Score 6\n"], "error": None},
        "buylist": {"replies": ["🟢 BUY Signals\n\nSYN000 (Score 6)\n"], "error": None},
        "selllist": {"replies": ["No SELL signals today."], "error": None},
        "strongbuy": {
            "replies": ["💪 STRONG BUY Signals\n\nSYN000 (Score 6, Conf 95%)\n"],
            "error": None,
        },
        "market": {
            "replies": ["📊 NEPSE Market Summary\n\n📈 Analyzed : 6\n⏭ Skipped  : 0\n\n🟢 BUY  : 1\n🟡 HOLD : 5\n🔴 SELL : 0"],
            "error": None,
        },
        "watchlist": {
            "replies": ["📋 Watchlist\n\n✅ SYN000\n"],
            "error": None,
        },
        "watchlist_scan": {
            "replies": ["🔍 Watchlist Scan\n\n📊 SYN000 | BUY | Score 6\n"],
            "error": None,
        },
        "portfolio": {
            "replies": ["💼 Portfolio Summary\n\n💰 Portfolio Value  : Rs. 1,000.00\n📥 Cost             : Rs. 900.00\n🟢 PnL              : Rs. 100.00\n📈 Return           : 11.11%\n📦 Holdings         : 1"],
            "error": None,
        },
        "backtest": {
            "replies": [
                "📈 Backtest: SYN000\n\n🕯 Candles  : 300\n🔁 Trades   : 2\n\n"
                "💰 Return      : +15.50%\n📉 Sharpe      : 0.85\n"
                "🛑 Max Drawdown: 5.67%\n\n✅ Win Rate  : 100.00%\n"
                "📊 Profit F. : 2.00\n🎯 Expectancy: +7.75%"
            ],
            "error": None,
        },
        "signals": {
            "replies": [
                "🧠 Signal Analysis: SYN000\n\n🟢 Signal: BUY (71% confidence)\n"
                "📊 Score: 6\n\n📝 SYN000 shows a BUY signal with 71% "
                "confidence (1 supporting factors)\n\n🔍 Reasons:\n"
                "• RSI at 45.0 — recovering from oversold zone\n\n"
                "💡 Recommendation: Consider accumulating on dips."
            ],
            "error": None,
        },
    }


class TestBotSection:
    def test_script_template_registers_all_handlers(self):
        """The spawned probe must wire every production command."""
        from benchmarks.production_gate import _BOT_CHECK_SCRIPT

        for name in (
            "start", "help", "analyze", "backtest", "signals", "top10",
            "buylist", "selllist", "strongbuy", "market", "watchlist",
            "portfolio",
        ):
            assert f"\"{name}\": bot." in _BOT_CHECK_SCRIPT
        assert "Application.builder().token(token).build()" in _BOT_CHECK_SCRIPT

    def test_contract_accepts_well_formed_replies(self):
        from benchmarks.production_gate import _validate_bot_results

        ok, why = _validate_bot_results(_well_formed_bot_results())
        assert ok, why

    def test_contract_flags_analyze_generic_error_reply(self):
        """The pre-fix /analyze (KeyError on buy_zone) replied with the
        generic error text — the contract validator must FAIL it."""
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["analyze"] = {"replies": ["Error processing request."], "error": None}
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "generic error reply" in why

    def test_contract_flags_handler_exception(self):
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["top10"] = {"replies": [], "error": "requests.Timeout: timed out"}
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "top10" in why and "requests.Timeout" in why

    def test_contract_flags_missing_registered_command(self):
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["registered"] = [c for c in results["registered"] if c != "market"]
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "registered commands mismatch" in why

    def test_contract_flags_missing_start_help_text(self):
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["start"] = {"replies": ["wrong text"], "error": None}
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "start" in why

    def test_contract_flags_empty_buylist_reply(self):
        """An empty reply is never valid — a handler that replied
        nothing (or crashed) must FAIL, never pass vacuously."""
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["buylist"] = {"replies": [], "error": None}
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "buylist" in why

    def test_contract_accepts_empty_state_replies_for_new_commands(self):
        """Documented empty-state replies (no signals / empty watchlist)
        are valid handler outcomes, not failures."""
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["strongbuy"] = {"replies": ["No STRONG BUY signals today."], "error": None}
        results["selllist"] = {"replies": ["No SELL signals today."], "error": None}
        results["watchlist"] = {"replies": ["Watchlist is empty."], "error": None}
        results["watchlist_scan"] = {
            "replies": ["Watchlist is empty — add symbols first."],
            "error": None,
        }
        results["backtest"] = {
            "replies": ["📈 Backtest: SYN000\n\nNo trades generated — no BUY signals matched."],
            "error": None,
        }
        ok, why = _validate_bot_results(results)
        assert ok, why

    def test_contract_flags_forbidden_generic_error_on_portfolio(self):
        """The generic error fallback must fail for EVERY command — even
        when a required marker also appears, it is never an acceptable
        /portfolio outcome."""
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["portfolio"] = {
            "replies": ["💼 Portfolio Summary\n\nError processing request."],
            "error": None,
        }
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "generic error reply" in why and "portfolio" in why

    def test_contract_flags_missing_new_command(self):
        """A new command missing from the payload (or the registered
        list) must FAIL — the contract is exact on all 12 commands."""
        from benchmarks.production_gate import _validate_bot_results

        results = _well_formed_bot_results()
        results["registered"] = [c for c in results["registered"] if c != "strongbuy"]
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "registered commands mismatch" in why

        results = _well_formed_bot_results()
        results.pop("portfolio", None)
        ok, why = _validate_bot_results(results)
        assert not ok
        assert "portfolio" in why

    def test_new_command_handlers_render_expected_replies(self, monkeypatch):
        """The new handlers render their documented headers against a
        mocked live API — regression lock for /strongbuy, /watchlist,
        /watchlist scan, and /portfolio."""
        import asyncio

        import src.bot.telegram_bot as bot

        payloads: dict[str, object] = {
            "/market/strongbuy": [
                {"symbol": "SYN000", "score": 6, "confidence": 95}
            ],
            "/watchlist": {"SYN000": {"enabled": True}},
            "/watchlist/scan": {
                "stocks_scanned": 1,
                "alerts": [],
                "results": [{"symbol": "SYN000", "signal": "BUY", "score": 6}],
            },
            "/portfolio/": {
                "portfolio_value": 1000.0,
                "portfolio_cost": 900.0,
                "portfolio_pnl": 100.0,
                "portfolio_return_pct": 11.11,
                "holdings": [{"symbol": "SYN000"}],
            },
        }

        class _FakeResp:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code != 200:
                    raise Exception(f"HTTP {self.status_code}")

        def _fake_get(url, *args, **kwargs):
            for path, payload in payloads.items():
                if url.endswith(path):
                    return _FakeResp(payload)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(bot, "API_BASE", "http://127.0.0.1:1")
        monkeypatch.setattr("requests.get", _fake_get)

        class _Msg:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text):
                self.replies.append(text)

        class _Upd:
            def __init__(self):
                self.message = _Msg()

        class _Ctx:
            def __init__(self, args=None):
                self.args = args or []

        async def _run(handler, args=None):
            upd, ctx = _Upd(), _Ctx(args)
            await handler(upd, ctx)
            return upd.message.replies[0]

        reply = asyncio.run(_run(bot.strongbuy))
        assert "💪 STRONG BUY Signals" in reply and "SYN000" in reply

        reply = asyncio.run(_run(bot.watchlist))
        assert "📋 Watchlist" in reply and "SYN000" in reply

        reply = asyncio.run(_run(bot.watchlist, ["scan"]))
        assert "🔍 Watchlist Scan" in reply and "SYN000" in reply

        reply = asyncio.run(_run(bot.portfolio))
        assert "💼 Portfolio Summary" in reply and "Rs. 1,000.00" in reply

    def test_backtest_and_signals_handlers_render_expected_replies(self, monkeypatch):
        """/backtest renders headline metrics computed from trade returns;
        /signals renders the /analyze payload plus the /signals/explain
        natural-language explanation (POST)."""
        import asyncio

        import src.bot.telegram_bot as bot

        payloads: dict[str, object] = {
            "/analyze/syn000": {
                "symbol": "SYN000",
                "signal": "BUY",
                "confidence": 71,
                "score": 6,
                "rsi": 45.0,
                "price": 100.0,
            },
            "/backtest/syn000": {
                "symbol": "/private/tmp/state/data/syn000.csv",
                "candles": 300,
                "total_trades": 2,
                "trades": [
                    {"return_pct": 10.0},
                    {"return_pct": 5.0},
                ],
                "metrics": {
                    "total_trades": 2,
                    "win_rate": 100.0,
                    "profit_factor": 2.0,
                    "expectancy": 7.5,
                },
                "report": {},
            },
        }

        class _FakeResp:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code != 200:
                    raise Exception(f"HTTP {self.status_code}")

        def _fake_get(url, *args, **kwargs):
            for path, payload in payloads.items():
                if url.endswith(path):
                    return _FakeResp(payload)
            raise AssertionError(f"unexpected GET: {url}")

        def _fake_post(url, *args, **kwargs):
            if url.endswith("/signals/explain"):
                return _FakeResp({
                    "success": True,
                    "data": {
                        "symbol": "SYN000",
                        "signal": "BUY",
                        "confidence": 71.0,
                        "reasons": ["RSI at 45.0 — recovering from oversold zone"],
                        "risks": [],
                        "summary": "SYN000 shows a BUY signal with 71% confidence (1 supporting factors)",
                        "recommendation": "Consider accumulating on dips.",
                    },
                })
            raise AssertionError(f"unexpected POST: {url}")

        monkeypatch.setattr(bot, "API_BASE", "http://127.0.0.1:1")
        monkeypatch.setattr("requests.get", _fake_get)
        monkeypatch.setattr("requests.post", _fake_post)

        class _Msg:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text):
                self.replies.append(text)

        class _Upd:
            def __init__(self):
                self.message = _Msg()

        class _Ctx:
            def __init__(self, args=None):
                self.args = args or []

        async def _run(handler, args=None):
            upd, ctx = _Upd(), _Ctx(args)
            await handler(upd, ctx)
            return upd.message.replies[0]

        reply = asyncio.run(_run(bot.backtest, ["SYN000"]))
        assert "📈 Backtest: SYN000" in reply
        assert "💰 Return      : +15.50%" in reply  # compounded 10% then 5%
        # calculate_sharpe_ratio([10.0, 5.0]): mean 7.5 / pstdev 2.5 = 3.00
        assert "📉 Sharpe      : 3.00" in reply
        assert "🛑 Max Drawdown: 0.00%" in reply  # monotone equity curve
        assert "Error processing request." not in reply

        reply = asyncio.run(_run(bot.signals, ["SYN000"]))
        assert "🧠 Signal Analysis: SYN000" in reply
        assert "Signal: BUY (71% confidence)" in reply
        assert "📊 Score: 6" in reply
        assert "Reasons:" in reply and "RSI at 45.0" in reply
        assert "Recommendation: Consider accumulating on dips." in reply
        assert "Error processing request." not in reply

    def test_section_bot_skips_without_telegram(self, monkeypatch):
        """python-telegram-bot is a hard dependency of the bot service; a
        missing install must SKIP (never a fake PASS)."""
        import importlib.util

        from benchmarks import production_gate

        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        sec = production_gate.section_bot()
        assert sec.status == SKIPPED
        assert "not installed" in sec.details["reason"]

    def test_bot_analyze_renders_entry_zone_not_buy_zone(self, monkeypatch):
        """Regression lock for the /analyze fix: the handler must render
        the production ``entry_zone`` string (no KeyError on
        ``buy_zone``) and guard None price/rsi/macd."""
        import asyncio

        import src.bot.telegram_bot as bot

        payload = {
            "symbol": "SYN000",
            "signal": "BUY",
            "confidence": 71,
            "price": 100.0,
            "entry_zone": "99.00 - 101.00",
            "stop_loss": 95.0,
            "target1": 105.0,
            "target2": 110.0,
            "target3": 115.0,
            "score": 6,
            "rsi": None,
            "macd": 0.0012,
        }

        class _FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bot, "API_BASE", "http://127.0.0.1:1")
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp())

        class _Msg:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text):
                self.replies.append(text)

        class _Upd:
            def __init__(self):
                self.message = _Msg()

        class _Ctx:
            args = ["SYN000"]

        update, context = _Upd(), _Ctx()
        asyncio.run(bot.analyze(update, context))
        reply = update.message.replies[0]
        assert "🛒 Buy Zone\n99.00 - 101.00" in reply
        assert "Error processing request." not in reply
        assert "📉 RSI : N/A" in reply  # None rsi must not crash the format
