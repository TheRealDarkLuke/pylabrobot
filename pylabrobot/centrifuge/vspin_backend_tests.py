import unittest
from unittest import mock

from pylabrobot.centrifuge.vspin_backend import (
  DOOR_UNLOCK_TO_OPEN_SETTLE_SECONDS,
  VSpinBackend,
  _with_vspin_checksum,
)


# status=0x11, current_position=12070, tachometer=-10, home_position=6733, checksum=0x29
_STATUS_PACKET = bytes.fromhex("11262f00004ff6ff184d1a000029")


def _make_backend(io: mock.Mock) -> VSpinBackend:
  backend = object.__new__(VSpinBackend)
  backend.io = io
  backend._command_set = "agilent"
  backend._bucket_1_remainder = None
  backend._last_command_at = 0.0
  return backend


def _make_old_backend(io: mock.Mock) -> VSpinBackend:
  backend = _make_backend(io)
  backend._command_set = "old_firmware"
  return backend


class VSpinCommandSetTests(unittest.IsolatedAsyncioTestCase):
  def test_default_command_set_is_agilent(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend()

    self.assertEqual(backend._command_set, "agilent")
    self.assertEqual(backend._get_command_bytes("open_door"), bytes.fromhex("aa022600062e"))
    self.assertEqual(backend._get_command_bytes("lock_bucket"), bytes.fromhex("aa022600072f"))

  def test_old_firmware_command_set_uses_legacy_pneumatic_commands(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend(command_set="old_firmware")

    self.assertEqual(backend._command_set, "old_firmware")
    self.assertEqual(backend._get_command_bytes("open_door"), bytes.fromhex("aa022600072f"))
    self.assertEqual(backend._get_command_bytes("close_door"), bytes.fromhex("aa022600052d"))
    self.assertEqual(backend._get_command_bytes("lock_bucket"), bytes.fromhex("aa0226000129"))
    self.assertEqual(backend._get_command_bytes("unlock_bucket"), bytes.fromhex("aa0226200048"))

  def test_velocity11_label_is_not_a_command_set(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      with self.assertRaisesRegex(ValueError, "command_set"):
        VSpinBackend(command_set="velocity11")

  def test_unknown_command_set_raises(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      with self.assertRaisesRegex(ValueError, "command_set"):
        VSpinBackend(command_set="velocity11-label")

  def test_with_vspin_checksum_repairs_final_byte(self):
    self.assertEqual(
      _with_vspin_checksum(bytes.fromhex("aa020e00")),
      bytes.fromhex("aa020e10"),
    )

  async def test_send_command_repairs_checksum_before_write(self):
    io = mock.Mock()
    io.read = mock.AsyncMock(return_value=b"\r")
    io.write = mock.AsyncMock(return_value=4)
    backend = _make_backend(io)

    await backend._send_command(bytes.fromhex("aa020e00"))

    io.write.assert_awaited_once_with(bytes.fromhex("aa020e10"))

  async def test_old_firmware_send_command_does_not_wait_for_carriage_return(self):
    io = mock.Mock()
    io.read = mock.AsyncMock(side_effect=[bytes.fromhex("0080d00151"), *([b""] * 20)])
    io.write = mock.AsyncMock(return_value=4)
    backend = _make_old_backend(io)

    response = await backend._send_command(bytes.fromhex("aa020e00"), read_timeout=0.04)

    self.assertEqual(response, bytes.fromhex("0080d00151"))
    io.write.assert_awaited_once_with(bytes.fromhex("aa020e10"))

  async def test_old_firmware_unlock_door_waits_before_opening(self):
    backend = _make_old_backend(mock.Mock())
    backend.get_door_locked = mock.AsyncMock(return_value=True)
    backend._send_command = mock.AsyncMock()
    sleep = mock.AsyncMock()

    with mock.patch("asyncio.sleep", sleep):
      await backend.unlock_door()

    backend._send_command.assert_awaited_once_with(bytes.fromhex("aa022600042c"))
    sleep.assert_awaited_once_with(DOOR_UNLOCK_TO_OPEN_SETTLE_SECONDS)

  def test_find_status_packet_parses_status_packet(self):
    parsed = VSpinBackend._find_status_packet(_STATUS_PACKET)

    assert parsed is not None
    self.assertEqual(parsed.status, 0x11)
    self.assertEqual(parsed.current_position, 12070)
    self.assertEqual(parsed.tachometer, -10)
    self.assertEqual(parsed.home_position, 6733)

  def test_find_status_packet_rejects_bad_checksum(self):
    packet = bytearray(_STATUS_PACKET)
    packet[-1] ^= 0xFF

    self.assertIsNone(VSpinBackend._find_status_packet(bytes(packet)))

  def test_find_status_byte_accepts_short_old_firmware_status(self):
    self.assertEqual(VSpinBackend._find_status_byte(bytes.fromhex("890808080849")), 0x89)
