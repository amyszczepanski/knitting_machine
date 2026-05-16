"""
tests/test_api.py — pytest unit tests for app/api.py.

Run with:
    pytest tests/test_api.py -v
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Patch sys.modules before any app.* imports so that heavy/hardware
# dependencies are never imported by the real import machinery.
# ---------------------------------------------------------------------------

_mock_disk = MagicMock()
_mock_disk.max_rows = 500
_mock_disk.read_pattern.return_value = []
_mock_disk.write_pattern.return_value = None
_mock_disk.to_disk_image_bytes.return_value = b"\x00" * 16

_mock_disk_image_cls = MagicMock()
_mock_disk_image_cls.blank.return_value = _mock_disk

_mock_machine_model = MagicMock()
_mock_machine_model.KH940 = "KH940"

from PIL import Image as _PIL_Image  # noqa: E402

_PIL_Image.preinit()

with patch.dict(
    "sys.modules",
    {
        "serial": MagicMock(),
        "serial.tools": MagicMock(),
        "serial.tools.list_ports": MagicMock(),
        "serial.tools.list_ports_common": MagicMock(),
        "app.brother_format": MagicMock(
            DiskImage=_mock_disk_image_cls,
            MachineModel=_mock_machine_model,
        ),
        "app.serial_emulator": MagicMock(),
        "app.ports": MagicMock(
            discover_ftdi_port=MagicMock(return_value=MagicMock(device="/dev/ttyUSB0")),
            list_all_ports=MagicMock(return_value=[]),
            PortDiscoveryError=Exception,
        ),
    },
):
    import app.api as _api_module
    from app.api import app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

sys.modules["app.api"] = _api_module
_state = _api_module._state
client = TestClient(app)

from .helpers import _make_png_bytes  # noqa: E402

# ===========================================================================
# api.py tests  (uses FastAPI TestClient)
# ===========================================================================


class TestListPatterns:
    def setup_method(self):
        _state.disk = _mock_disk

    def test_empty_disk_returns_empty_list(self):
        _mock_disk.read_pattern.return_value = []
        resp = client.get("/patterns")
        assert resp.status_code == 200
        assert resp.json()["patterns"] == []

    def test_pattern_present_is_listed(self):
        # Simulate pattern 901 having 3 rows of 10 stitches
        def _read(number):
            if number == 901:
                return [[1] * 10] * 3
            return []

        _mock_disk.read_pattern.side_effect = _read
        resp = client.get("/patterns")
        assert resp.status_code == 200
        patterns = resp.json()["patterns"]
        assert any(p["number"] == 901 and p["rows"] == 3 for p in patterns)
        # Reset
        _mock_disk.read_pattern.side_effect = None
        _mock_disk.read_pattern.return_value = []


class TestWritePattern:
    def setup_method(self):
        _state.disk = _mock_disk

    def _upload(
        self, number: int = 901, threshold: int = 128, png: bytes | None = None
    ):
        png = png or _make_png_bytes()
        return client.post(
            "/pattern",
            data={"number": str(number), "threshold": str(threshold)},
            files={"file": ("test.png", png, "image/png")},
        )

    def test_valid_upload_returns_200(self):
        resp = self._upload()
        assert resp.status_code == 200
        body = resp.json()
        assert body["number"] == 901
        assert body["width"] > 0
        assert body["height"] > 0

    def test_invalid_pattern_number_returns_422(self):
        resp = self._upload(number=800)  # out of 901–999 range
        assert resp.status_code == 422

    def test_empty_file_returns_400(self):
        resp = client.post(
            "/pattern",
            data={"number": 901},
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert resp.status_code == 400

    def test_bad_image_bytes_returns_422(self):
        resp = client.post(
            "/pattern",
            data={"number": 901},
            files={"file": ("bad.png", b"not an image", "image/png")},
        )
        assert resp.status_code == 422


class TestPreview:
    def setup_method(self):
        _state.disk = _mock_disk

    def test_valid_image_returns_data_uri(self):

        resp = client.post(
            "/preview",
            data={"threshold": "128"},
            files={"file": ("test.png", _make_png_bytes(), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_uri"].startswith("data:image/png;base64,")
        assert body["width"] > 0
        assert body["height"] > 0

    def test_data_uri_is_valid_base64_png(self):
        import base64
        import io
        from PIL import Image

        resp = client.post(
            "/preview",
            data={},
            files={
                "file": ("test.png", _make_png_bytes(width=20, height=20), "image/png")
            },
        )
        assert resp.status_code == 200
        uri = resp.json()["data_uri"]
        b64 = uri.split(",", 1)[1]
        png_bytes = base64.b64decode(b64)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"

    def test_bad_image_returns_422(self):
        resp = client.post(
            "/preview",
            data={},
            files={"file": ("bad.png", b"garbage", "image/png")},
        )
        assert resp.status_code == 422

    def test_empty_file_returns_400(self):
        resp = client.post(
            "/preview",
            data={},
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert resp.status_code == 400


class TestSendStatus:
    def setup_method(self):
        _state.tasks.clear()
        _state.serial_port = "/dev/ttyUSB0"

    def teardown_method(self):
        _state.serial_port = ""

    def _post_send(self):
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            return client.post("/send")

    def test_unknown_task_id_returns_404(self):
        resp = client.get("/send/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_send_returns_task_id(self):
        resp = self._post_send()
        assert resp.status_code == 200
        body = resp.json()
        assert "task_id" in body
        assert body["status"] in ("pending", "running", "done", "error")

    def test_send_status_reachable_after_send(self):
        send_resp = self._post_send()
        assert send_resp.status_code == 200, send_resp.json()
        task_id = send_resp.json()["task_id"]
        status_resp = client.get(f"/send/{task_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["task_id"] == task_id


class TestConfig:
    def test_get_config_returns_defaults(self):
        resp = client.get("/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "serial_port" in body
        assert "baud_rate" in body
        assert "disk_dir" in body

    def test_put_config_updates_serial_port(self):
        resp = client.put("/config", json={"serial_port": "/dev/ttyUSB1"})
        assert resp.status_code == 200
        assert resp.json()["serial_port"] == "/dev/ttyUSB1"
        # Restore
        client.put("/config", json={"serial_port": "/dev/ttyUSB0"})

    def test_put_config_updates_baud_rate(self):
        resp = client.put("/config", json={"baud_rate": 19200})
        assert resp.status_code == 200
        assert resp.json()["baud_rate"] == 19200
        # Restore
        client.put("/config", json={"baud_rate": 9600})

    def test_put_config_partial_update_leaves_other_fields(self):
        # Only update disk_dir; serial_port should stay the same
        before = client.get("/config").json()["serial_port"]
        client.put("/config", json={"disk_dir": "/tmp/new_dir"})
        after = client.get("/config").json()["serial_port"]
        assert before == after
        # Restore
        client.put("/config", json={"disk_dir": "/tmp/knitting_disk"})


class TestResetDisk:
    def setup_method(self):
        _state.disk = _mock_disk

    def test_delete_disk_returns_ok(self):
        resp = client.delete("/disk")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_patterns_empty_after_reset(self):
        _mock_disk.read_pattern.return_value = []
        client.delete("/disk")
        resp = client.get("/patterns")
        assert resp.json()["patterns"] == []
