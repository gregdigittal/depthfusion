"""Tests for DeviceRegistry + devices CLI.

Covers:
- register creates a row; get returns it
- get returns None for an unknown device_id
- list_all returns rows newest-first
- revoke marks a device as revoked and returns True
- revoke returns False for an unknown device_id
- touch updates last_sync
- thread safety: 10 concurrent register calls produce no exception
- CLI cmd_list prints devices
- CLI cmd_list returns 1 when no devices
- CLI cmd_revoke revokes a device
- CLI cmd_revoke returns 1 when device not found
- CLI main dispatches list / revoke sub-commands
- CLI main prints help on no args / --help
- CLI main returns 2 on unknown sub-command
- CLI main returns 2 for revoke without device_id
"""
from __future__ import annotations

import sys
import threading
import time
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from depthfusion.identity.device_registry import DeviceRecord, DeviceRegistry
from depthfusion.cli.devices import cmd_list, cmd_revoke, main


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_registry(tmp_path: Path) -> DeviceRegistry:
    return DeviceRegistry(db_path=tmp_path / "identity.db")


def register_device(
    registry: DeviceRegistry,
    device_id: str = "dev-001",
    owner: str = "principal-aaa",
    platform: str = "linux",
) -> DeviceRecord:
    return registry.register(device_id, owner, platform)


# ---------------------------------------------------------------------------
# DeviceRegistry unit tests
# ---------------------------------------------------------------------------


class TestDeviceRegistry:
    def test_register_and_get(self, tmp_path: Path) -> None:
        """register followed by get returns the same record."""
        registry = make_registry(tmp_path)
        rec = register_device(registry, "dev-001", "principal-aaa", "linux")

        result = registry.get("dev-001")
        assert result is not None
        assert result.device_id == "dev-001"
        assert result.owner_principal_id == "principal-aaa"
        assert result.platform == "linux"
        assert result.revoked is False
        assert result.last_sync > 0

    def test_get_unknown_returns_none(self, tmp_path: Path) -> None:
        """get returns None for a device_id not in the store."""
        registry = make_registry(tmp_path)
        assert registry.get("no-such-device") is None

    def test_list_all_returns_newest_first(self, tmp_path: Path) -> None:
        """list_all orders records by last_sync descending."""
        registry = make_registry(tmp_path)
        for i in range(3):
            register_device(registry, f"dev-{i:03d}")
            time.sleep(0.01)

        records = registry.list_all()
        assert len(records) == 3
        assert records[0].device_id == "dev-002"
        assert records[1].device_id == "dev-001"
        assert records[2].device_id == "dev-000"

    def test_revoke_marks_device_revoked(self, tmp_path: Path) -> None:
        """revoke sets revoked=True and returns True."""
        registry = make_registry(tmp_path)
        register_device(registry, "dev-rev")

        result = registry.revoke("dev-rev")
        assert result is True

        record = registry.get("dev-rev")
        assert record is not None
        assert record.revoked is True

    def test_revoke_unknown_returns_false(self, tmp_path: Path) -> None:
        """revoke returns False when device_id does not exist."""
        registry = make_registry(tmp_path)
        assert registry.revoke("ghost-device") is False

    def test_touch_updates_last_sync(self, tmp_path: Path) -> None:
        """touch increases last_sync for an existing device."""
        registry = make_registry(tmp_path)
        register_device(registry, "dev-touch")

        first = registry.get("dev-touch")
        assert first is not None
        first_ts = first.last_sync

        time.sleep(0.05)
        updated = registry.touch("dev-touch")
        assert updated is True

        second = registry.get("dev-touch")
        assert second is not None
        assert second.last_sync > first_ts

    def test_touch_unknown_returns_false(self, tmp_path: Path) -> None:
        """touch returns False when device_id does not exist."""
        registry = make_registry(tmp_path)
        assert registry.touch("ghost-device") is False

    def test_register_replaces_existing(self, tmp_path: Path) -> None:
        """Calling register twice with the same device_id replaces the row."""
        registry = make_registry(tmp_path)
        register_device(registry, "dev-dup", "owner-a", "linux")
        register_device(registry, "dev-dup", "owner-b", "darwin")

        record = registry.get("dev-dup")
        assert record is not None
        assert record.owner_principal_id == "owner-b"
        assert record.platform == "darwin"
        assert record.revoked is False

    def test_default_platform_uses_sys_platform(self, tmp_path: Path) -> None:
        """When platform is omitted, sys.platform is used."""
        registry = make_registry(tmp_path)
        registry.register("dev-plat", "owner-x")

        record = registry.get("dev-plat")
        assert record is not None
        assert record.platform == sys.platform

    def test_thread_safety(self, tmp_path: Path) -> None:
        """10 threads registering concurrently must not raise."""
        registry = make_registry(tmp_path)
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                registry.register(
                    f"dev-t{idx:02d}",
                    f"principal-t{idx:02d}",
                    "linux",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(registry.list_all()) == 10


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_cmd_list_prints_devices(self, tmp_path: Path, capsys) -> None:
        """cmd_list prints a table when devices exist."""
        db = tmp_path / "identity.db"
        registry = DeviceRegistry(db_path=db)
        registry.register("dev-alpha", "owner-1", "linux")

        rc = cmd_list(db_path=db)
        assert rc == 0

        out, _ = capsys.readouterr()
        assert "dev-alpha" in out
        assert "owner-1" in out

    def test_cmd_list_returns_1_when_empty(self, tmp_path: Path, capsys) -> None:
        """cmd_list returns 1 and prints a message when no devices exist."""
        db = tmp_path / "identity.db"
        DeviceRegistry(db_path=db)  # create DB but register nothing

        rc = cmd_list(db_path=db)
        assert rc == 1

        out, _ = capsys.readouterr()
        assert "No devices" in out

    def test_cmd_revoke_success(self, tmp_path: Path, capsys) -> None:
        """cmd_revoke prints confirmation and returns 0 when device exists."""
        db = tmp_path / "identity.db"
        registry = DeviceRegistry(db_path=db)
        registry.register("dev-beta", "owner-2", "linux")

        rc = cmd_revoke("dev-beta", db_path=db)
        assert rc == 0

        out, _ = capsys.readouterr()
        assert "dev-beta" in out
        assert "revoked" in out.lower()

        assert registry.get("dev-beta").revoked is True  # type: ignore[union-attr]

    def test_cmd_revoke_not_found(self, tmp_path: Path, capsys) -> None:
        """cmd_revoke returns 1 and prints an error when device not found."""
        db = tmp_path / "identity.db"
        DeviceRegistry(db_path=db)

        rc = cmd_revoke("ghost", db_path=db)
        assert rc == 1

        _, err = capsys.readouterr()
        assert "not found" in err.lower()

    def test_main_list(self, tmp_path: Path, capsys) -> None:
        """main(['list']) dispatches to cmd_list."""
        db = tmp_path / "identity.db"
        registry = DeviceRegistry(db_path=db)
        registry.register("dev-main", "owner-m", "linux")

        with mock.patch(
            "depthfusion.cli.devices._default_db_path", return_value=db
        ):
            rc = main(["list"])

        assert rc == 0
        out, _ = capsys.readouterr()
        assert "dev-main" in out

    def test_main_revoke(self, tmp_path: Path, capsys) -> None:
        """main(['revoke', <id>]) dispatches to cmd_revoke."""
        db = tmp_path / "identity.db"
        registry = DeviceRegistry(db_path=db)
        registry.register("dev-r", "owner-r", "linux")

        with mock.patch(
            "depthfusion.cli.devices._default_db_path", return_value=db
        ):
            rc = main(["revoke", "dev-r"])

        assert rc == 0
        assert registry.get("dev-r").revoked is True  # type: ignore[union-attr]

    def test_main_help(self, capsys) -> None:
        """main([]) and main(['--help']) print usage and return 0."""
        for args in ([], ["--help"]):
            rc = main(args)
            assert rc == 0
            out, _ = capsys.readouterr()
            assert "Usage" in out

    def test_main_unknown_subcommand(self, capsys) -> None:
        """main(['bogus']) returns 2 and prints an error."""
        rc = main(["bogus"])
        assert rc == 2
        _, err = capsys.readouterr()
        assert "unknown" in err.lower()

    def test_main_revoke_missing_id(self, capsys) -> None:
        """main(['revoke']) without a device_id returns 2."""
        rc = main(["revoke"])
        assert rc == 2
        _, err = capsys.readouterr()
        assert "device_id" in err.lower() or "argument" in err.lower()
