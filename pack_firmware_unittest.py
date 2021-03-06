#!/usr/bin/env python
# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
from contextlib import contextmanager
import os
import sys
import unittest

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

from chromite.lib import cros_build_lib_unittest
from chromite.lib import partial_mock
import chromite.lib.cros_logging as logging
from chromite.lib import osutils
import pack_firmware
from pack_firmware import PackFirmware, PackError

# Disable all logging as it's confusing to get log output from tests.
logging.getLogger().setLevel(logging.CRITICAL + 1)

# Pre-set IDs for files in test/
RO_FRID = 'Google_Reef.9264.0.2017_02_09_1240'
VBUTIL_OUTPUT = '''Key block:
  Size:                2232
  Flags:               7 (ignored)
  Data key algorithm:  7 RSA4096 SHA256
  Data key version:    1
  Data key sha1sum:    e2c1c92d7d7aa7dfed5e8375edd30b7ae52b7450
Preamble:
  Size:                  2164
  Header version:        2.1
  Firmware version:      1
  Kernel key algorithm:  7 RSA4096 SHA256
  Kernel key version:    1
  Kernel key sha1sum:    5d2b220899c4403d564092ada3f12d3cc4483223
  Firmware body size:    920000
  Preamble flags:        1
Body verification succeeded.
'''
FMAP_OUTPUT = '''WP_RO 0 4194304
SI_DESC 0 4096
IFWI 4096 2093056
RO_VPD 2097152 16384
RO_SECTION 2113536 2080768
FMAP 2113536 2048
RO_FRID 2115584 64
RO_FRID_PAD 2115648 1984
COREBOOT 2117632 1552384
GBB 3670016 262144
RO_UNUSED 3932160 262144
MISC_RW 4194304 196608
UNIFIED_MRC_CACHE 4194304 135168
RECOVERY_MRC_CACHE 4194304 65536
RW_MRC_CACHE 4259840 65536
RW_VAR_MRC_CACHE 4325376 4096
RW_ELOG 4329472 12288
RW_SHARED 4341760 16384
SHARED_DATA 4341760 8192
VBLOCK_DEV 4349952 8192
RW_VPD 4358144 8192
RW_NVRAM 4366336 24576
RW_SECTION_A 4390912 4718592
VBLOCK_A 4390912 65536
FW_MAIN_A 4456448 4652992
RW_FWID_A 9109440 64
RW_SECTION_B 9109504 4718592
VBLOCK_B 9109504 65536
FW_MAIN_B 9175040 4652992
RW_FWID_B 13828032 64
RW_LEGACY 13828096 2097152
BIOS_UNUSABLE 15925248 323584
DEVICE_EXTENSION 16248832 524288
UNUSED_HOLE 16773120 4096
'''
ECRW_SIZE = 0xb7f00
FMAP_OUTPUT_EC = '''EC_RO 64 229376
FR_MAIN 64 229376
RO_FRID 388 32
FMAP 135232 350
WP_RO 0 262144
EC_RW 262144 229376
RW_FWID 262468 32
'''

# Use this to suppress stdout/stderr output:
# with capture_sys_output() as (stdout, stderr)
#   ...do something...
@contextmanager
def capture_sys_output():
    capture_out, capture_err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = capture_out, capture_err
        yield capture_out, capture_err
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class TestUnit(unittest.TestCase):
  def setUp(self):
    self.pack = PackFirmware('.')

  def testBadStartup(self):
    """Test various bad start-up conditions"""
    # Starting up in another directory (without required files) should fail.
    with self.assertRaises(PackError) as e:
      pack_firmware.main(['/'])
    self.assertIn("required file '/pack_dist/updater.sh'", str(e.exception))

    # Should check for 'shar' tool.
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('type shar'), returncode=1)
      with self.assertRaises(PackError) as e:
        pack_firmware.main(['.'])
      self.assertIn("You need 'shar'", str(e.exception))

    # Should complain about missing tools.
    with self.assertRaises(PackError) as e:
      pack_firmware.main(['.', '--script=updater5.sh',
                          '--tools', 'missing-tool',])
    self.assertIn("Cannot find tool program 'missing-tool'", str(e.exception))

    # Should complain if we don't provide at least one image.
    with self.assertRaises(PackError) as e:
      args = ['.', '--script=updater5.sh', '--tools', 'ls',
              '--tool_base', '/bin']
      pack_firmware.main(args)
    self.assertIn('Must assign at least one of BIOS', str(e.exception))

  def testArgParse(self):
    """Test some basic argument parsing as a sanity check."""
    with self.assertRaises(SystemExit):
      with capture_sys_output() as (stdout, stderr):
        self.assertEqual(None, self.pack.ParseArgs(['--invalid']))

    self.assertEqual(None, self.pack.ParseArgs([]).bios_image)
    self.assertEqual('bios.bin',
                     self.pack.ParseArgs(['-b', 'bios.bin']).bios_image)

    self.assertEqual(False, self.pack.ParseArgs([]).remove_inactive_updaters)
    self.assertEqual(True,
                     self.pack.ParseArgs(['--remove_inactive_updaters'])
                         .remove_inactive_updaters)

    self.assertEqual(True, self.pack.ParseArgs([]).merge_bios_rw_image)
    self.assertEqual(True, self.pack.ParseArgs(['--merge_bios_rw_image'])
                         .merge_bios_rw_image)
    self.assertEqual(False, self.pack.ParseArgs(['--no-merge_bios_rw_image'])
                         .merge_bios_rw_image)

  def testEnsureCommand(self):
    self.pack._EnsureCommand('ls', 'sample-package')
    with self.assertRaises(PackError):
      self.pack._EnsureCommand('does-not-exist', 'sample-package')

  def testTmpdirs(self):
    dir1 = self.pack._GetTmpdir()
    dir2 = self.pack._GetTmpdir()
    self.assertTrue(os.path.exists(dir1))
    self.assertTrue(os.path.exists(dir2))
    self.pack._RemoveTmpdirs()
    self.assertFalse(os.path.exists(dir1))
    self.assertFalse(os.path.exists(dir2))

  def testExtractFrid(self):
    self.pack._tmpdir = 'test'
    self.pack._args = self.pack.ParseArgs(['--bios_image', 'image.bin'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap'), returncode=0)
      self.assertEqual(RO_FRID, self.pack._ExtractFrid('image.bin'))

  def testAddFlashromVersion(self):
    self.pack._args = self.pack.ParseArgs(['--tool_base', 'test'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
          output='ELF 64-bit LSB executable, etc.\n')
      self.pack._AddFlashromVersion()
    result = self.pack._versions.getvalue().splitlines()
    self.assertIn('flashrom(8)', result[0])
    self.assertIn('ELF 64-bit LSB executable', result[1])
    self.assertEqual('%s0.9.4  : 1bb61e1 : Feb 07 2017 18:29:17 UTC' %
                     (' ' * 13), result[2])

  def _AddMocks(self, rc):
    rc.AddCmdResult(partial_mock.ListRegex('type shar'), returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
        output='ELF 64-bit LSB executable, etc.\n')
    rc.AddCmdResult(partial_mock.Regex('dump_fmap -x test/image.bin'),
                    returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('gbb_utility'), returncode=0,
        output=' - exported root_key to file: rootkey.bin')
    rc.AddCmdResult(partial_mock.ListRegex('vbutil_firmware'), returncode=0,
        output=VBUTIL_OUTPUT)
    rc.AddCmdResult(partial_mock.ListRegex('resign_firmwarefd.sh'),
                    returncode=0)
    rc.AddCmdResult(partial_mock.Regex('dump_fmap -x .*bios_rw.bin'),
                    returncode=0)
    rc.AddCmdResult(partial_mock.Regex('--sb_repack'),
                    returncode=0, output=FMAP_OUTPUT_EC)

  def testMockedRun(self):
    """Starting up with a valid updater script and BIOS should work."""

    args = ['.', '--script=updater5.sh', '--tools', 'flashrom dump_fmap',
            '--tool_base', 'test', '-b', 'test/image.bin',
            '--create_bios_rw_image', '-e', 'test/ec.bin', '-o' 'out']
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      pack_firmware.main(args)
      result = pack_firmware.pack._versions.getvalue().splitlines()

  def testMockedRunWithMerge(self):
    """Starting up with a valid updater script and BIOS should work."""
    def _CreateFile(cmd, **kwargs):
      file_arg = cmd.index('-f')
      fname = cmd[file_arg + 1]
      with open(fname, 'wb') as fd:
        fd.seek(ECRW_SIZE - 1)
        fd.write('\0')

    args = ['.', '--script=updater5.sh', '--tools', 'flashrom dump_fmap',
            '--tool_base', 'test', '-b', 'test/image.bin',
            '--bios_rw_image', 'test/image_rw.bin', '--merge_bios_rw_image',
            '-e', 'test/ec.bin', '-p', 'test/pd.bin',
            '--remove_inactive_updaters', '-o' 'out']
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -x .*test/image_rw.bin'),
                      returncode=0)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -p test/image_rw.bin'),
                      returncode=0, output=FMAP_OUTPUT)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -p .*bios.bin'),
                      returncode=0, output=FMAP_OUTPUT)
      rc.AddCmdResult(partial_mock.Regex('extract_ecrw'), returncode=0)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -p .*ec.bin'),
                      returncode=0, output=FMAP_OUTPUT_EC)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -x .*EC_MAIN_A'),
                      returncode=0)
      rc.AddCmdResult(partial_mock.Regex('cbfstool'), returncode=0,
                      side_effect=_CreateFile)
      rc.AddCmdResult(partial_mock.Regex('dump_fmap -p .*pd.bin'),
                      returncode=0, output=FMAP_OUTPUT_EC)
      pack_firmware.main(args)
      result = pack_firmware.pack._versions.getvalue().splitlines()
      print('\n'.join(result))

if __name__ == '__main__':
    unittest.main()
