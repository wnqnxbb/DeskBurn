"""测试稳定设备名和本地绑定配置。"""

import json
import tempfile
import unittest
from pathlib import Path

from ccswitch_agent import protocol
from ccswitch_agent.binding import (
    BindingError,
    MultipleDevicesError,
    choose_initial_binding,
    clear_binding,
    load_binding,
    save_binding,
)


class DeviceNameTest(unittest.TestCase):
    def test_accepts_firmware_generated_name(self) -> None:
        self.assertTrue(protocol.is_device_name("DeskBurn-70AF0986B648"))

    def test_rejects_legacy_or_ambiguous_names(self) -> None:
        self.assertFalse(protocol.is_device_name("DeskBurn"))
        self.assertFalse(protocol.is_device_name("DeskBurn-B6B648"))
        self.assertFalse(protocol.is_device_name("DeskBurn-70af0986b648"))
        self.assertFalse(protocol.is_device_name("Other-70AF0986B648"))


class BindingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = Path(self._temporary.name) / "nested" / "device.json"

    def test_missing_binding_returns_none(self) -> None:
        self.assertIsNone(load_binding(self.path))

    def test_save_load_and_clear_round_trip(self) -> None:
        name = "DeskBurn-70AF0986B648"
        save_binding(name, self.path)

        self.assertEqual(load_binding(self.path), name)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())
        self.assertTrue(clear_binding(self.path))
        self.assertFalse(clear_binding(self.path))
        self.assertIsNone(load_binding(self.path))

    def test_refuses_invalid_name(self) -> None:
        with self.assertRaises(BindingError):
            save_binding("DeskBurn", self.path)

    def test_reports_corrupt_json(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(BindingError, "配置损坏"):
            load_binding(self.path)

    def test_reports_invalid_name_in_json(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"device_name": "DeskBurn"}), encoding="utf-8"
        )

        with self.assertRaisesRegex(BindingError, "名称无效"):
            load_binding(self.path)


class InitialSelectionTest(unittest.TestCase):
    def test_waits_when_no_device_is_visible(self) -> None:
        self.assertIsNone(choose_initial_binding([]))

    def test_automatically_selects_only_device(self) -> None:
        self.assertEqual(
            choose_initial_binding(["DeskBurn-70AF0986B648"]),
            "DeskBurn-70AF0986B648",
        )

    def test_refuses_to_guess_between_multiple_devices(self) -> None:
        with self.assertRaisesRegex(MultipleDevicesError, "--bind"):
            choose_initial_binding([
                "DeskBurn-70AF0986B648",
                "DeskBurn-112233445566",
            ])


if __name__ == "__main__":
    unittest.main()
