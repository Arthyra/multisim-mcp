from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from multisim_unified_mcp.native_worker import EditorFrida, render_bitmap


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


class BitmapExportTest(TestCase):
    def setUp(self):
        self.args = {
            "path": "capture.bmp", "render_scale": 1.5, "show_frame": False, "show_grid": False,
            "region": {"left": -20, "top": 40, "right": 200, "bottom": 120},
        }
        self.bounds = {"left": 40, "top": 50, "right": 100, "bottom": 80}
        self.call = Mock(side_effect=[{"content_bounds": self.bounds}, {"ready": True}])

    def test_crops_native_pixels_at_original_origin(self):
        image = MagicMock()
        bitmap = image.open.return_value.__enter__.return_value
        bitmap.width, bitmap.height = 300, 180
        with patch.dict("sys.modules", {"PIL": Mock(Image=image)}):
            result = render_bitmap(self.call, "exportInteractiveImage", self.args)
        operation, render_args = self.call.call_args.args
        self.assertEqual(operation, "exportInteractiveImage")
        self.assertEqual(render_args["region"], {"left": 0, "top": 0, "right": 200, "bottom": 120})
        self.assertEqual(result["region"], self.args["region"])
        self.assertEqual((result["width"], result["height"]), (330, 120))
        bitmap.crop.assert_called_once_with((51, 66, 159, 129))
        image.new.return_value.paste.assert_called_once_with(bitmap.crop.return_value, (81, 6))

    def test_preserves_native_rendering_for_actual_off_sheet_content(self):
        self.bounds["left"] = -10
        render_bitmap(self.call, "exportImage", self.args)
        self.call.assert_called_with("exportImage", self.args)

    def test_waiting_for_live_frame_does_not_read_bitmap(self):
        waiting = {"ready": False, "state": 1}
        self.call.side_effect = [{"content_bounds": self.bounds}, waiting]
        self.assertIs(render_bitmap(self.call, "exportInteractiveImage", self.args), waiting)
