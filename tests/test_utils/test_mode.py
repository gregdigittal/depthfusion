import warnings

from depthfusion.utils.mode import normalise_mode


def test_local():
    assert normalise_mode("local") == "local"


def test_vps_cpu():
    assert normalise_mode("vps-cpu") == "vps-cpu"


def test_vps_gpu():
    assert normalise_mode("vps-gpu") == "vps-gpu"


def test_vps_legacy_alias_emits_deprecation():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = normalise_mode("vps")
    assert result == "vps-cpu"
    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)
    assert "deprecated" in str(w[0].message).lower()


def test_none_defaults_to_local():
    assert normalise_mode(None) == "local"


def test_empty_string_defaults_to_local():
    assert normalise_mode("") == "local"


def test_unknown_mode_falls_back_to_local():
    assert normalise_mode("something-unknown") == "local"
