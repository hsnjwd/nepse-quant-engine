"""Tests for scripts/refresh_corpus.py (Sprint 13 — corpus refresh tooling).

Hermetic by construction: every test uses synthetic shard payloads and
``tmp_path`` data dirs; network access is replaced by monkeypatched
``refresh_corpus.fetch_shard`` / ``_urlopen``.  Nothing here touches the
real ``data/raw`` corpus or the live yonepse host.
"""

from __future__ import annotations

import datetime
import re
import threading

import pytest

from scripts.refresh_corpus import (
    _atomic_write,
    _fmt,
    append_bar,
    fetch_shard,
    find_freshest_shard,
    main,
    merge_bars,
    next_run_delay,
    parse_shard_bars,
    run_scheduler,
)

NABIL_BAR = {
    "Date": "2026-08-12",
    "Open": 552.0,
    "High": 552.0,
    "Low": 550.0,
    "Close": 551.0,
    "Volume": 300,
}

SYNTH_SHARD = {
    "date": "2026-08-12",
    "series": {
        "NABIL": [
            [0, 552.0, 100, 1000.0, 5],
            [1, 550.0, 200, 2100.0, 9],
            [2, 551.0, 300, 3100.0, 12],
        ],
        "ADBL": [[0, 304.3, 50, 500.0, 2], [1, 304.6, 70, 720.0, 4]],
    },
}


# ---------------------------------------------------------------------------
# parse_shard_bars
# ---------------------------------------------------------------------------


class TestParseShardBars:
    def test_derives_ohlc_from_intraday_series(self):
        bars = parse_shard_bars(SYNTH_SHARD)
        assert bars["NABIL"] == {
            "Date": "2026-08-12",
            "Open": 552.0,
            "High": 552.0,
            "Low": 550.0,
            "Close": 551.0,
            "Volume": 300,
        }
        assert bars["ADBL"] == {
            "Date": "2026-08-12",
            "Open": 304.3,
            "High": 304.6,
            "Low": 304.3,
            "Close": 304.6,
            "Volume": 70,
        }

    def test_single_row_bar_collapses_ohlc(self):
        bars = parse_shard_bars(
            {"date": "2026-08-12", "series": {"X": [[0, 10.5, 7, 1.0, 1]]}}
        )
        assert bars["X"] == {
            "Date": "2026-08-12",
            "Open": 10.5,
            "High": 10.5,
            "Low": 10.5,
            "Close": 10.5,
            "Volume": 7,
        }

    def test_skips_empty_and_malformed_series(self):
        shard = {
            "date": "2026-08-12",
            "series": {
                "EMPTY": [],
                "JUNK": [[0, 1.0]],  # too short
                "NOTLIST": "oops",
                "OK": [[0, 100.0, 10, 1.0, 1]],
            },
        }
        bars = parse_shard_bars(shard)
        assert set(bars) == {"OK"}

    def test_missing_series_or_date_returns_empty(self):
        assert parse_shard_bars({"date": "2026-08-12"}) == {}
        assert parse_shard_bars({"series": {"A": [[0, 1.0, 2, 1.0, 1]]}}) == {}
        assert parse_shard_bars({}) == {}


# ---------------------------------------------------------------------------
# append_bar
# ---------------------------------------------------------------------------


class TestAppendBar:
    def test_appends_new_date_and_preserves_existing_rows(self, tmp_path):
        p = tmp_path / "nabil.csv"
        p.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-14,525.1,532.9,525.1,530.1,76835\n",
            encoding="utf-8",
        )
        added, dup = append_bar(p, NABIL_BAR)
        assert (added, dup) == (1, 0)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "Date,Open,High,Low,Close,Volume",
            "2026-07-14,525.1,532.9,525.1,530.1,76835",
            "2026-08-12,552,552,550,551,300",
        ]

    def test_dedups_existing_date_and_leaves_file_untouched(self, tmp_path):
        p = tmp_path / "nabil.csv"
        original = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-12,552,552,550,551,300\n"
        )
        p.write_text(original, encoding="utf-8")
        added, dup = append_bar(p, NABIL_BAR)
        assert (added, dup) == (0, 1)
        assert p.read_text(encoding="utf-8") == original

    def test_preserves_crlf_terminator(self, tmp_path):
        p = tmp_path / "nabil.csv"
        # ``newline=""`` on write and a raw byte read on the assertions keep
        # the literal CRLFs intact (otherwise Windows text mode would
        # translate them and the fixture/assertions would see ``\r\r\n`` /
        # stripped newlines).
        p.write_text(
            "Date,Open,High,Low,Close,Volume\r\n"
            "2026-07-14,525.1,532.9,525.1,530.1,76835\r\n",
            encoding="utf-8",
            newline="",
        )
        append_bar(p, NABIL_BAR)
        text = p.read_bytes().decode("utf-8")
        assert "\n" not in text.replace("\r\n", "")  # no stray bare LF
        assert text.endswith("\r\n")
        assert "2026-08-12,552,552,550,551,300\r\n" in text

    def test_skips_missing_file_without_creating_it(self, tmp_path):
        p = tmp_path / "ghost.csv"
        added, dup = append_bar(p, NABIL_BAR)
        assert (added, dup) == (0, 0)
        assert not p.exists()

    def test_skips_file_with_unrecognized_header(self, tmp_path, capsys):
        p = tmp_path / "odd.csv"
        p.write_text("Symbol,Whatever\nX,1\n", encoding="utf-8")
        added, dup = append_bar(p, NABIL_BAR)
        assert (added, dup) == (0, 0)
        assert "unrecognized header" in capsys.readouterr().err

    def test_dry_run_reports_but_writes_nothing(self, tmp_path):
        p = tmp_path / "nabil.csv"
        original = "Date,Open,High,Low,Close,Volume\n2026-07-14,525.1,532.9,525.1,530.1,76835\n"
        p.write_text(original, encoding="utf-8")
        added, dup = append_bar(p, NABIL_BAR, dry_run=True)
        assert (added, dup) == (1, 0)
        assert p.read_text(encoding="utf-8") == original

    def test_atomic_write_leaves_no_tmp_and_handles_empty_file(self, tmp_path):
        p = tmp_path / "nabil.csv"
        p.write_text("", encoding="utf-8")
        added, dup = append_bar(p, NABIL_BAR)
        assert (added, dup) == (1, 0)
        assert not list(tmp_path.glob("*.tmp"))
        assert p.read_text(encoding="utf-8").splitlines() == [
            "Date,Open,High,Low,Close,Volume",
            "2026-08-12,552,552,550,551,300",
        ]

    def test_atomic_write_retries_windows_lock(self, tmp_path, monkeypatch):
        """os.replace can intermittently fail on Windows (antivirus briefly
        holding the temp file) — _atomic_write must retry, not die.

        The success path is a deterministic stand-in (no delegation to the
        real os.replace), so the test cannot be derailed by the very
        antivirus lock it simulates.
        """
        from pathlib import Path

        p = tmp_path / "x.csv"
        state = {"calls": 0}

        def flaky_replace(src, dst):
            state["calls"] += 1
            if state["calls"] == 1:
                raise PermissionError(5, "Access is denied")
            dst.write_bytes(Path(src).read_bytes())
            Path(src).unlink(missing_ok=True)

        monkeypatch.setattr("scripts.refresh_corpus.os.replace", flaky_replace)
        _atomic_write(p, "hello\n")
        assert state["calls"] == 2  # exactly one retry after the flake
        assert p.read_text(encoding="utf-8") == "hello\n"
        assert not list(tmp_path.glob("*.tmp"))

    def test_atomic_write_uses_replace(self, tmp_path, monkeypatch):
        p = tmp_path / "x.csv"
        p.write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")
        replaced = []

        import os

        real_replace = os.replace  # captured BEFORE the patch (see below)

        def fake_replace(src, dst):
            replaced.append((os.path.basename(src), os.path.basename(dst)))
            assert os.path.exists(src)
            real_replace(src, dst)  # must not call the patched os.replace

        # ``scripts.refresh_corpus.os`` IS the global os module, so the
        # patch affects every ``os.replace`` call site during the test — the
        # fake therefore delegates to the captured real implementation.
        monkeypatch.setattr("scripts.refresh_corpus.os.replace", fake_replace)
        append_bar(p, NABIL_BAR)
        assert replaced == [("x.csv.tmp", "x.csv")]
        assert not (tmp_path / "x.csv.tmp").exists()


# ---------------------------------------------------------------------------
# merge_bars (batch merge used by the backfill)
# ---------------------------------------------------------------------------


class TestMergeBars:
    def _row(self, date_str, close=550.0, volume=100):
        return {
            "Date": date_str,
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": volume,
        }

    def test_merges_multiple_bars_in_one_write(self, tmp_path):
        p = tmp_path / "nabil.csv"
        p.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-04,500,505,498,502,1000\n",
            encoding="utf-8",
        )
        added, dup = merge_bars(
            p, [self._row("2026-05-05"), self._row("2026-05-06", close=560.0)]
        )
        assert (added, dup) == (2, 0)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "Date,Open,High,Low,Close,Volume",
            "2026-05-04,500,505,498,502,1000",
            "2026-05-05,550,550,550,550,100",
            "2026-05-06,560,560,560,560,100",
        ]

    def test_dedups_against_existing_and_within_batch(self, tmp_path):
        p = tmp_path / "nabil.csv"
        p.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-05,550,550,550,550,100\n",
            encoding="utf-8",
        )
        bars = [
            self._row("2026-05-05"),  # dup with the existing row
            self._row("2026-05-05"),  # dup within the batch
            self._row("2026-05-06"),
        ]
        added, dup = merge_bars(p, bars)
        assert (added, dup) == (1, 2)
        assert "2026-05-06,550,550,550,550,100" in p.read_text(encoding="utf-8")

    def test_no_write_when_everything_duplicate(self, tmp_path):
        p = tmp_path / "nabil.csv"
        original = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-05,550,550,550,550,100\n"
        )
        p.write_text(original, encoding="utf-8")
        added, dup = merge_bars(p, [self._row("2026-05-05")])
        assert (added, dup) == (0, 1)
        assert p.read_text(encoding="utf-8") == original

    def test_dry_run_reports_but_writes_nothing(self, tmp_path):
        p = tmp_path / "nabil.csv"
        original = "Date,Open,High,Low,Close,Volume\n"
        p.write_text(original, encoding="utf-8")
        added, dup = merge_bars(p, [self._row("2026-05-05")], dry_run=True)
        assert (added, dup) == (1, 0)
        assert p.read_text(encoding="utf-8") == original

    def test_skips_missing_file(self, tmp_path):
        added, dup = merge_bars(tmp_path / "ghost.csv", [self._row("2026-05-05")])
        assert (added, dup) == (0, 0)
        assert not (tmp_path / "ghost.csv").exists()


# ---------------------------------------------------------------------------
# fetch_shard / find_freshest_shard
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status=200, payload=None, exc=None):
        self.status = status
        self._payload = payload
        self._exc = exc

    def __enter__(self):
        if self._exc is not None:
            raise self._exc
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        if self._payload is None:
            return b""
        return json_bytes(self._payload)


def json_bytes(payload):
    import json

    return json.dumps(payload).encode()


def fake_urlopen(status=200, payload=None, exc=None):
    from urllib.error import HTTPError

    def _open(url, timeout):
        if exc is not None:
            if isinstance(exc, int):
                raise HTTPError(url, exc, "err", {}, None)
            raise exc
        return _FakeResp(status=status, payload=payload)

    return _open


class TestFetchShard:
    def test_ok_payload(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.refresh_corpus._urlopen",
            fake_urlopen(payload={"date": "2026-08-12", "series": {}}),
        )
        payload, kind = fetch_shard("https://example.test", "2026-08-12")
        assert kind == "ok"
        assert payload["date"] == "2026-08-12"

    def test_404_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.refresh_corpus._urlopen", fake_urlopen(exc=404)
        )
        _, kind = fetch_shard("https://example.test", "2026-01-01")
        assert kind == "not_found"

    def test_other_http_error_returns_http(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.refresh_corpus._urlopen", fake_urlopen(exc=500)
        )
        _, kind = fetch_shard("https://example.test", "2026-08-12")
        assert kind == "http"

    def test_network_error_returns_http(self, monkeypatch):
        import urllib.error

        monkeypatch.setattr(
            "scripts.refresh_corpus._urlopen",
            fake_urlopen(exc=urllib.error.URLError("down")),
        )
        _, kind = fetch_shard("https://example.test", "2026-08-12")
        assert kind == "http"

    def test_wrong_shape_returns_shape(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.refresh_corpus._urlopen",
            fake_urlopen(payload={"hello": "world"}),
        )
        _, kind = fetch_shard("https://example.test", "2026-08-12")
        assert kind == "shape"


class TestFindFreshestShard:
    def test_picks_latest_available_day(self, monkeypatch):
        # Only 2026-08-10 exists: the 11th and 12th are non-trading days
        # (404), so the scan must walk back to the 10th.
        def fake_fetch(base, date_str):
            if date_str == "2026-08-10":
                return {"date": date_str, "series": {}}, "ok"
            return None, "not_found"

        monkeypatch.setattr("scripts.refresh_corpus.fetch_shard", fake_fetch)
        date_str, shard, note = find_freshest_shard(
            "https://example.test", today=datetime.date(2026, 8, 12)
        )
        assert date_str == "2026-08-10"
        assert shard is not None
        assert note is None

    def test_fails_fast_on_dead_host(self, monkeypatch):
        calls = []

        def fake_fetch(base, date_str):
            calls.append(date_str)
            return None, "http"

        monkeypatch.setattr("scripts.refresh_corpus.fetch_shard", fake_fetch)
        date_str, shard, note = find_freshest_shard(
            "https://example.test", days=14, today=datetime.date(2026, 8, 12)
        )
        assert (date_str, shard) == (None, None)
        assert "unreachable" in note
        assert len(calls) == 2  # abort after MAX_CONSECUTIVE_ERRORS

    def test_404s_do_not_count_against_fail_fast(self, monkeypatch):
        calls = []

        def fake_fetch(base, date_str):
            calls.append(date_str)
            return None, "not_found"

        monkeypatch.setattr("scripts.refresh_corpus.fetch_shard", fake_fetch)
        date_str, shard, note = find_freshest_shard(
            "https://example.test", days=3, today=datetime.date(2026, 8, 12)
        )
        assert (date_str, shard) == (None, None)
        assert "no daily shard" in note
        assert len(calls) == 3  # every day probed, no early abort


# ---------------------------------------------------------------------------
# main (end-to-end through the CLI, network-free)
# ---------------------------------------------------------------------------


class TestMain:
    def _corpus(self, tmp_path):
        d = tmp_path / "raw"
        d.mkdir()
        (d / "nabil.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-14,525.1,532.9,525.1,530.1,76835\n",
            encoding="utf-8",
        )
        (d / "adbl.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-14,304.3,304.6,303.9,304.6,26094\n",
            encoding="utf-8",
        )
        # A symbol that will have no bar in the shard:
        (d / "ghost.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n", encoding="utf-8"
        )
        return d

    def test_dry_run_reports_and_writes_nothing(self, tmp_path, capsys, monkeypatch):
        d = self._corpus(tmp_path)
        before = {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.csv")}

        from scripts import refresh_corpus

        # Use the explicit --date path so only fetch_shard is exercised.
        monkeypatch.setattr(
            refresh_corpus, "fetch_shard", lambda base, date: (SYNTH_SHARD, "ok")
        )
        rc = main(["--data-dir", str(d), "--date", "2026-08-12", "--dry-run"])

        assert rc == 0
        after = {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.csv")}
        assert after == before
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "Rows appended:      2" in out
        assert "Shard date:         2026-08-12" in out

    def test_real_run_appends_and_is_idempotent(self, tmp_path, monkeypatch):
        from scripts import refresh_corpus

        d = self._corpus(tmp_path)
        monkeypatch.setattr(
            refresh_corpus, "fetch_shard", lambda base, date: (SYNTH_SHARD, "ok")
        )
        rc1 = main(["--data-dir", str(d), "--date", "2026-08-12"])
        rc2 = main(["--data-dir", str(d), "--date", "2026-08-12"])

        assert rc1 == 0 and rc2 == 0
        nabil = (d / "nabil.csv").read_text(encoding="utf-8")
        assert nabil.count("2026-08-12") == 1  # appended once, dedup on re-run
        assert "2026-08-12,552,552,550,551,300" in nabil
        adbl = (d / "adbl.csv").read_text(encoding="utf-8")
        assert "2026-08-12,304.3,304.6,304.3,304.6,70" in adbl
        ghost = (d / "ghost.csv").read_text(encoding="utf-8")
        assert "2026-08-12" not in ghost  # no bar for GHOST — untouched
        assert not list(d.glob("*.tmp"))  # no leftover temp files

    def test_error_when_data_dir_missing(self, tmp_path, capsys):
        assert main(["--data-dir", str(tmp_path / "nope")]) == 1
        assert "not found" in capsys.readouterr().err

    def test_error_when_shard_unavailable(self, tmp_path, capsys, monkeypatch):
        from scripts import refresh_corpus

        d = self._corpus(tmp_path)
        monkeypatch.setattr(
            refresh_corpus, "fetch_shard", lambda base, date: (None, "not_found")
        )
        rc = main(["--data-dir", str(d), "--date", "2026-08-12"])
        assert rc == 1
        assert "unavailable" in capsys.readouterr().err

    def test_freshest_path_renders_note(self, tmp_path, capsys, monkeypatch):
        from scripts import refresh_corpus

        d = self._corpus(tmp_path)
        monkeypatch.setattr(
            refresh_corpus,
            "find_freshest_shard",
            lambda base, days: ("2026-08-10", SYNTH_SHARD, None),
        )
        rc = main(["--data-dir", str(d), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Shard date:         2026-08-10" in out
        assert "freshest within the last 14 days" in out
        assert "DRY RUN" in out


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------


class TestFmt:
    def test_integral_floats_print_without_decimal(self):
        assert _fmt(552.0) == "552"
        assert _fmt(300.0) == "300"

    def test_fractional_floats_and_ints(self):
        assert _fmt(525.1) == "525.1"
        assert _fmt(22309) == "22309"


# ---------------------------------------------------------------------------
# _atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_and_replaces(self, tmp_path):
        p = tmp_path / "f.csv"
        _atomic_write(p, "hello\n")
        assert p.read_text(encoding="utf-8") == "hello\n"
        assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# scheduler (next_run_delay / run_scheduler)
# ---------------------------------------------------------------------------


def _summary_assert(out: str, label: str, value: int) -> None:
    """Assert the backfill summary contains ``label: <spaces>value``.

    Column-aligned prints make exact-substring assertions fragile to a
    single-space drift; a regex on label + numeric value is robust.
    """
    assert re.search(
        rf"^{label}:?\s+{value}$", out, re.MULTILINE
    ), f"{label} != {value} in output:\n{out}"


class _FakeClock:
    """Injectable clock for run_scheduler tests (advances with sleep)."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + datetime.timedelta(seconds=seconds)


class TestNextRunDelay:
    def test_at_time_ahead_returns_until_today(self):
        now = datetime.datetime(2026, 8, 12, 10, 0, 0)
        assert next_run_delay("16:00", now) == pytest.approx(6 * 3600.0)

    def test_at_time_passed_wraps_to_tomorrow(self):
        now = datetime.datetime(2026, 8, 12, 17, 0, 0)
        assert next_run_delay("16:00", now) == pytest.approx(23 * 3600.0)

    def test_at_time_now_rolls_to_tomorrow(self):
        now = datetime.datetime(2026, 8, 12, 16, 0, 0)
        assert next_run_delay("16:00", now) == pytest.approx(24 * 3600.0)

    def test_empty_at_time_uses_interval(self):
        now = datetime.datetime(2026, 8, 12, 10, 0, 0)
        assert next_run_delay("", now, interval_hours=1.5) == pytest.approx(5400.0)

    def test_invalid_at_time_falls_back_to_interval(self):
        now = datetime.datetime(2026, 8, 12, 10, 0, 0)
        assert next_run_delay("garbage", now, interval_hours=2.0) == pytest.approx(7200.0)

    def test_non_positive_interval_is_floored_to_60s(self):
        """A 0/negative interval (operator misconfig) must never turn the
        scheduler into a busy loop — the delay is floored at 60 s."""
        now = datetime.datetime(2026, 8, 12, 10, 0, 0)
        assert next_run_delay("", now, interval_hours=0) >= 60.0
        assert next_run_delay("", now, interval_hours=-1) >= 60.0
        assert next_run_delay("garbage", now, interval_hours=0) >= 60.0


class TestRunScheduler:
    def test_runs_immediately_then_at_next_scheduled_time(self):
        clock = _FakeClock(datetime.datetime(2026, 8, 12, 10, 0, 0))
        stop = threading.Event()
        calls = []

        def on_run():
            calls.append(clock.now)
            if len(calls) == 2:
                stop.set()

        rc = run_scheduler(
            on_run,
            at_time="16:00",
            stop_event=stop,
            sleep_fn=clock.advance,
            now_fn=clock,
        )
        assert rc == 0
        assert calls == [
            datetime.datetime(2026, 8, 12, 10, 0, 0),
            datetime.datetime(2026, 8, 12, 16, 0, 0),
        ]

    def test_stop_event_set_after_first_run_skips_sleep(self):
        clock = _FakeClock(datetime.datetime(2026, 8, 12, 10, 0, 0))
        stop = threading.Event()
        calls = []

        def on_run():
            calls.append(1)
            stop.set()

        run_scheduler(
            on_run,
            at_time="16:00",
            stop_event=stop,
            sleep_fn=clock.advance,
            now_fn=clock,
        )
        assert len(calls) == 1
        assert clock.now == datetime.datetime(2026, 8, 12, 10, 0, 0)

    def test_pre_set_stop_event_never_runs(self):
        stop = threading.Event()
        stop.set()
        calls = []
        run_scheduler(
            lambda: calls.append(1),
            stop_event=stop,
            sleep_fn=lambda s: None,
            now_fn=lambda: datetime.datetime(2026, 8, 12, 10, 0, 0),
        )
        assert calls == []

    def test_failed_cycle_does_not_kill_scheduler(self, capsys):
        clock = _FakeClock(datetime.datetime(2026, 8, 12, 10, 0, 0))
        stop = threading.Event()
        state = {"n": 0}

        def on_run():
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("network down")
            if state["n"] == 2:
                stop.set()

        run_scheduler(
            on_run,
            at_time="16:00",
            stop_event=stop,
            sleep_fn=clock.advance,
            now_fn=clock,
        )
        assert state["n"] == 2  # a second cycle still happened
        assert "cycle failed" in capsys.readouterr().err


class TestMainSchedule:
    def test_schedule_mode_defers_to_run_scheduler(self, tmp_path, monkeypatch):
        from scripts import refresh_corpus

        d = tmp_path / "raw"
        d.mkdir()
        captured: dict = {}

        def fake_run_scheduler(on_run, **kwargs):
            captured["on_run"] = on_run
            captured.update(kwargs)
            return 0

        monkeypatch.setattr(refresh_corpus, "run_scheduler", fake_run_scheduler)
        rc = main(["--data-dir", str(d), "--schedule", "--at-time", "09:30"])
        assert rc == 0
        assert captured["at_time"] == "09:30"
        assert captured["interval_hours"] == 24.0
        assert callable(captured["on_run"])  # the per-cycle pass
        # The CLI lets run_scheduler create its own stop event — only the
        # launcher thread injects one (covered by TestCorpusRefresher).
        assert "stop_event" not in captured


# ---------------------------------------------------------------------------
# run_backfill_once (--backfill)
# ---------------------------------------------------------------------------


def _backfill_shard(date_str):
    """Single-row synthetic shard: OHLC all equal the close, fixed volume."""
    return {
        "date": date_str,
        "series": {
            "NABIL": [[0, 550.0, 100, 0.0, 1]],
            "ADBL": [[0, 301.0, 50, 0.0, 1]],
        },
    }


class TestRunBackfill:
    def _corpus(self, tmp_path):
        d = tmp_path / "raw"
        d.mkdir()
        (d / "nabil.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-04,500,505,498,502,1000\n",
            encoding="utf-8",
        )
        (d / "adbl.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-04,300,301,299,300,500\n",
            encoding="utf-8",
        )
        return d

    def _patched_fetch(self, monkeypatch, ok_days_back=3):
        """fetch_shard answers 'ok' for the last *ok_days_back* days of the
        window and 404 otherwise — relative to the real clock, so the test
        is deterministic regardless of when it runs."""
        from scripts import refresh_corpus

        today = datetime.date.today()

        def fake_fetch(base_url, date_str):
            d = datetime.date.fromisoformat(date_str)
            if d >= today - datetime.timedelta(days=ok_days_back - 1):
                return _backfill_shard(date_str), "ok"
            return None, "not_found"

        monkeypatch.setattr(refresh_corpus, "fetch_shard", fake_fetch)

    def test_backfills_missing_days_sorted(self, tmp_path, monkeypatch, capsys):
        d = self._corpus(tmp_path)
        self._patched_fetch(monkeypatch, ok_days_back=4)
        rc = main(
            [
                "--backfill",
                "--days",
                "5",
                "--data-dir",
                str(d),
                "--workers",
                "1",
                "--base-url",
                "http://x",
            ]
        )
        assert rc == 0
        nabil = (d / "nabil.csv").read_text(encoding="utf-8").splitlines()
        assert len(nabil) == 6  # header + 1 existing + 4 recovered days
        assert nabil[0] == "Date,Open,High,Low,Close,Volume"
        assert nabil[1] == "2026-05-04,500,505,498,502,1000"
        # ISO dates sort lexicographically — the whole file stays ordered.
        assert all(nabil[i] < nabil[i + 1] for i in range(1, len(nabil) - 1))
        out = capsys.readouterr().out
        _summary_assert(out, "Rows appended", 8)  # 2 files x 4 rows
        _summary_assert(out, "Shards recovered", 4)
        _summary_assert(out, "Shards failed", 0)
        assert not list(d.glob("*.tmp"))

    def test_backfill_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        d = self._corpus(tmp_path)
        self._patched_fetch(monkeypatch)
        before = {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.csv")}
        rc = main(
            [
                "--backfill",
                "--days",
                "5",
                "--data-dir",
                str(d),
                "--workers",
                "1",
                "--base-url",
                "http://x",
                "--dry-run",
            ]
        )
        assert rc == 0
        after = {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.csv")}
        assert after == before

    def test_backfill_is_idempotent(self, tmp_path, monkeypatch):
        d = self._corpus(tmp_path)
        self._patched_fetch(monkeypatch, ok_days_back=3)
        argv = [
            "--backfill",
            "--days",
            "5",
            "--data-dir",
            str(d),
            "--workers",
            "1",
            "--base-url",
            "http://x",
        ]
        assert main(argv) == 0
        first = (d / "nabil.csv").read_text(encoding="utf-8")
        assert main(argv) == 0
        assert (d / "nabil.csv").read_text(encoding="utf-8") == first

    def test_failed_shard_is_isolated_and_counted(
        self, tmp_path, monkeypatch, capsys
    ):
        from scripts import refresh_corpus

        d = self._corpus(tmp_path)
        today = datetime.date.today()

        def fake_fetch(base_url, date_str):
            d_ = datetime.date.fromisoformat(date_str)
            if d_ == today - datetime.timedelta(days=1):
                return None, "http"  # transient failure mid-window
            if d_ >= today - datetime.timedelta(days=2):
                return _backfill_shard(date_str), "ok"
            return None, "not_found"

        monkeypatch.setattr(refresh_corpus, "fetch_shard", fake_fetch)
        rc = main(
            [
                "--backfill",
                "--days",
                "5",
                "--data-dir",
                str(d),
                "--workers",
                "1",
                "--base-url",
                "http://x",
            ]
        )
        assert rc == 0  # a failed day never aborts the backfill
        out = capsys.readouterr().out
        _summary_assert(out, "Shards failed", 1)
        _summary_assert(out, "Rows appended", 4)  # 2 files x 2 recovered days

    def test_error_without_data_dir(self, tmp_path, capsys):
        assert main(["--backfill", "--data-dir", str(tmp_path / "nope")]) == 1
        assert "not found" in capsys.readouterr().err
