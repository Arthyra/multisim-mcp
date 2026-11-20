from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from multisim_unified_mcp.native_worker import EditorFrida


class NativeProfileTest(TestCase):
    def setUp(self):
        self.install = Path("verified-install").resolve()
        self.image = str(self.install / "multisim.exe")
        self.frida = Mock()
        self.runtime_status = Mock(return_value={"available": True, "compatible": True})
        self.editor = EditorFrida(self.frida, self.install, self.runtime_status)

    def test_verified_install_can_attach(self):
        with patch("multisim_unified_mcp.native_worker.multisim_processes", return_value={7: (None, self.image)}):
            self.assertIs(self.editor.attach(7), self.frida.attach.return_value)
        self.frida.attach.assert_called_once_with(7)

    def test_incompatible_installed_modules_block_attach(self):
        self.runtime_status.return_value["compatible"] = False
        with patch("multisim_unified_mcp.native_worker.multisim_processes", return_value={7: (None, self.image)}):
            with self.assertRaisesRegex(RuntimeError, "module fingerprints"):
                self.editor.attach(7)
        self.frida.attach.assert_not_called()

    def test_unidentified_or_different_process_blocks_attach(self):
        for image in (None, str(self.install.parent / "other-install" / "multisim.exe")):
            with self.subTest(image=image):
                with patch("multisim_unified_mcp.native_worker.multisim_processes", return_value={7: (None, image)}):
                    with self.assertRaisesRegex(RuntimeError, "configured Multisim executable"):
                        self.editor.attach(7)
        self.frida.attach.assert_not_called()
