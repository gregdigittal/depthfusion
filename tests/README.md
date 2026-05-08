# DepthFusion — Test Suite

## Test-vs-Production Path Separation (S-82)

### The problem

`MetricsCollector()` (default constructor) writes telemetry to
`~/.claude/depthfusion-metrics/`.  Before S-82, bare `MetricsCollector()`
calls in test code polluted that directory with test-fixture events.  Over
a 13-day dogfood window, 987/987 observed events (100%) were test noise.

### The fix

`tests/conftest.py` provides an **autouse session fixture**
(`_guard_metrics_production_path`) that patches `MetricsCollector.__init__`
for the entire test session.  Any call to `MetricsCollector()` without an
explicit `metrics_dir` is redirected to a per-session temporary directory
instead of `~/.claude/depthfusion-metrics/`.

Production code is not modified.  The guard lives in test infrastructure.

### How the guard works

The fixture compares `Path.home()` at call time against `_REAL_HOME` (the
home directory captured at conftest import time, before any test monkeypatching):

- `Path.home() == _REAL_HOME` (no redirect active) → intercepted; writes go
  to a per-session temp dir.
- `Path.home() != _REAL_HOME` (test has redirected home) → fixture steps aside;
  the test's own isolation mechanism takes effect.

### Rule for new test authors

**Preferred:** Pass an explicit `metrics_dir` when constructing
`MetricsCollector` in a test.  Use pytest's built-in `tmp_path` fixture:

```python
def test_my_feature(tmp_path):
    collector = MetricsCollector(metrics_dir=tmp_path / "metrics")
    collector.record("my.metric", 1.0)
    ...
```

**Also acceptable:** The `monkeypatch.setattr(Path, "home", ...)` pattern
used by the integration tests — the conftest fixture detects the redirect and
lets it work:

```python
def test_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Production code that calls MetricsCollector() internally will write
    # to tmp_path / ".claude" / "depthfusion-metrics"
    ...
```

### Escape hatch for integration tests that explicitly need the production path

Pass the path explicitly — the guard only intercepts the `metrics_dir=None` case:

```python
def test_prod_path_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    prod_path = tmp_path / ".claude" / "depthfusion-metrics"
    collector = MetricsCollector(metrics_dir=prod_path)
    ...
```

### Where the fixture lives

`tests/conftest.py` — `_guard_metrics_production_path` (autouse, session scope)

### Tests verifying the guard

`tests/test_metrics/test_path_isolation.py` — S-82 AC-4 / T-276

| Test | What it verifies |
|------|-----------------|
| `test_default_constructor_does_not_write_to_production_dir` | Bare `MetricsCollector()` does not write to `~/.claude/` |
| `test_default_constructor_writes_to_test_temp_dir` | Default writes go to a temp dir, not real home |
| `test_production_directory_absent_or_unmodified_after_default_call` | Belt-and-suspenders: no new files in prod dir |
| `test_explicit_metrics_dir_is_respected` | Explicit `metrics_dir` bypasses the guard |
| `test_explicit_metrics_dir_receives_records` | Records land in the explicitly supplied directory |
| `test_explicit_prod_path_bypasses_guard_intentionally` | Documented escape hatch works |
| `test_home_redirect_pattern_works_with_autouse_fixture` | Home-redirect integration test pattern is unaffected |

## Running the tests

```bash
# All metrics tests (fast, hermetic)
python -m pytest tests/test_metrics/ -q

# Full suite
python -m pytest -q
```
