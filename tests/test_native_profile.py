from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from multisim_unified_mcp.native_worker import (
    EditorFrida, distant_power_labels, missing_global_supplies, render_bitmap, reuse_editor_views,
)


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


class PowerLabelLayoutTest(TestCase):
    def setUp(self):
        self.page = {"left": 0, "top": 0, "right": 1000, "bottom": 800}
        self.body = {"left": 504, "top": 450, "right": 522, "bottom": 468}
        self.label = {"id": "label", "kind": "reference", "text": "0",
                      "bounds": {"left": 459, "top": 405, "right": 470, "bottom": 421}}
        self.symbol = {"id": "symbol", "ref": "0", "body_bounds": self.body,
                       "labels": [self.label], "power_symbol": True}

    def test_reports_the_displaced_ground_label_and_a_repair(self):
        result = distant_power_labels([self.symbol], [], self.page, Mock(return_value=False))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "distant_power_label")
        self.assertEqual(result[0]["suggested_arguments"],
                         {"component_ref": "symbol", "kind": "reference", "x": 534, "y": 459})

    def test_ignores_non_power_labels_and_nearby_power_labels(self):
        regular_part = {**self.symbol, "power_symbol": False}
        nearby_label = {**self.label, "bounds": {"left": 528, "top": 450, "right": 539, "bottom": 466}}
        nearby_power = {**self.symbol, "labels": [nearby_label]}
        self.assertEqual(distant_power_labels([regular_part, nearby_power], [], self.page, Mock()), [])

    def test_suggestion_avoids_crossing_a_wire(self):
        wire = {"points": [{"x": 530, "y": 440}, {"x": 530, "y": 480}]}
        intersects = Mock(side_effect=[True, False])
        result = distant_power_labels([self.symbol], [wire], self.page, intersects)
        self.assertEqual(result[0]["suggested_arguments"]["x"], 492)


class HiddenPowerCheckTest(TestCase):
    def setUp(self):
        self.parts = [{"ref": "U1", "hidden_power_pins": [
            {"name": "GND", "number": "8", "net": "0"},
            {"name": "VCC", "number": "16", "net": "vcc"},
        ]}]
        self.bridge = ["xU1.2C3", "VCC", "dU1.2C3", "U1_OPEN_VCC", "U1_OPEN_GND", "TTL_LSRCV__NON__2"]

    def test_bridge_does_not_supply_the_hidden_pin(self):
        diagnostic, = missing_global_supplies(self.parts, [self.bridge], [])
        self.assertEqual(diagnostic["node"], "VCC")
        self.assertEqual(diagnostic["components"], [{"ref": "U1", "pin": "16", "name": "VCC"}])

    def test_native_source_and_existing_diagnostics_are_respected(self):
        source = ["VCCVCC", "VCC", "0", "dc", "5"]
        self.assertEqual(missing_global_supplies(self.parts, [self.bridge, source], []), [])
        self.assertEqual(missing_global_supplies(self.parts, [self.bridge], [{"node": "vcc"}]), [])


class EditorViewReuseTest(TestCase):
    def test_reuses_activation_without_changing_sheet_selection(self):
        source = (
            "const view=method('DrwEngine.dll','?ShowSheet@CProject@@','pointer',"
            "['pointer','uint8','uint8'],'mscdecl')(treeInstance(),0,1);\n"
            "for(const sheet of sheets){selectSheet({index:sheet.index});"
            "states.push({sheet_index:sheet.index,...circuitState(args)});}\n"
            "activateSheet();"
        )
        patched = reuse_editor_views(source)
        self.assertIn("diagram().candidates.filter(s=>/CSCH_CAPSubSheet/.test(s.name)).length===1?1:0,1)", patched)
        self.assertIn("selectSheet({index:sheet.index})", patched)
        self.assertTrue(patched.endswith("activateSheet();"))
