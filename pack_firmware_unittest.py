# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for pack_firmware.py.

This mocks out all tools so it can run fairly quickly.
"""

from __future__ import print_function

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
import pack_firmware

# Disable all logging as it's confusing to get log output from tests.
logging.getLogger().setLevel(logging.CRITICAL + 1)

# Pre-set ID expected for test/image.bin.
RO_FRID = 'Google_Reef.9264.0.2017_02_09_1240'

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
  """Test cases for common program flows."""
  def setUp(self):
    self.packer = pack_firmware.FirmwarePacker('.')

  def testBadStartup(self):
    """Test various bad start-up conditions"""
    # Starting up in another directory (without required files) should fail.
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(['/'])
    self.assertIn("'/pack_dist/updater.sh'", str(e.exception))

    # Should check for 'shar' tool.
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('type shar'), returncode=1)
      with self.assertRaises(pack_firmware.PackError) as e:
        pack_firmware.main(['.'])
      self.assertIn("'shar'", str(e.exception))

    # Should complain about missing tools.
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(['.', '--script=updater5.sh',
                          '--tools', 'missing-tool',])
    self.assertIn("'missing-tool'", str(e.exception))

    # Should complain if we don't provide at least one image.
    with self.assertRaises(pack_firmware.PackError) as e:
      args = ['.', '--script=updater5.sh', '--tools', 'ls',
              '--tool_base', '/bin']
      pack_firmware.main(args)
    self.assertIn('Must assign at least one', str(e.exception))

  def testArgParse(self):
    """Test some basic argument parsing as a sanity check."""
    with self.assertRaises(SystemExit):
      with capture_sys_output():
        self.assertEqual(None, self.packer.ParseArgs(['--invalid']))

    self.assertEqual(None, self.packer.ParseArgs([]).bios_image)
    self.assertEqual('bios.bin',
                     self.packer.ParseArgs(['-b', 'bios.bin']).bios_image)

    self.assertEqual(True, self.packer.ParseArgs([]).remove_inactive_updaters)
    self.assertEqual(False,
                     self.packer.ParseArgs(['--no-remove_inactive_updaters'])
                     .remove_inactive_updaters)

    self.assertEqual(True, self.packer.ParseArgs([]).merge_bios_rw_image)
    self.assertEqual(True, self.packer.ParseArgs(['--merge_bios_rw_image'])
                     .merge_bios_rw_image)
    self.assertEqual(False, self.packer.ParseArgs(['--no-merge_bios_rw_image'])
                     .merge_bios_rw_image)

  def testEnsureCommand(self):
    """Check that we detect a missing command."""
    self.packer._EnsureCommand('ls', 'sample-package')
    with self.assertRaises(pack_firmware.PackError) as e:
      self.packer._EnsureCommand('does-not-exist', 'sample-package')
    self.assertIn("You need 'does-not-exist'", str(e.exception))

  def testFindTool(self):
    """Check finding of required tools."""
    self.packer._args = self.packer.ParseArgs(['--tool_base', 'test'])
    with self.assertRaises(pack_firmware.PackError) as e:
      self.packer._FindTool('does-not-exist')
    self.assertIn("'does-not-exist'", str(e.exception))
    self.packer._FindTool('flashrom')

  def testTmpdirs(self):
    """Check creation and removal of temporary directories."""
    dir1 = self.packer._CreateTmpDir()
    dir2 = self.packer._CreateTmpDir()
    self.assertTrue(os.path.exists(dir1))
    self.assertTrue(os.path.exists(dir2))
    self.packer._RemoveTmpdirs()
    self.assertFalse(os.path.exists(dir1))
    self.assertFalse(os.path.exists(dir2))

  def testAddFlashromVersion(self):
    """Test we can add the flashrom version to the version information."""
    self.packer._args = self.packer.ParseArgs(['--tool_base', 'test'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
                      output='ELF 64-bit LSB executable, etc.\n')
      self.packer._AddFlashromVersion()
    result = self.packer._versions.getvalue().splitlines()
    self.assertIn('flashrom(8)', result[1])
    self.assertIn('ELF 64-bit LSB executable', result[2])
    self.assertEqual('%s0.9.4  : 1bb61e1 : Feb 07 2017 18:29:17 UTC' %
                     (' ' * 13), result[3])

  def testAddVersionInfoMissingFile(self):
    """Trying to add version info for a missing file should be detected."""
    with self.assertRaises(IOError) as e:
      self.packer._AddVersionInfo('BIOS', 'missing-file', 'v123')
    self.assertIn("'missing-file'", str(e.exception))

  def testAddVersionInfoNoFile(self):
    """Check adding version info with no filename."""
    self.packer._AddVersionInfo('BIOS', '', 'v123')
    self.assertEqual('BIOS version: v123\n', self.packer._versions.getvalue())

  def testAddVersionNoVersion(self):
    """Check adding version info with no version."""
    self.packer._AddVersionInfo('BIOS', 'test/image.bin', '')
    self.assertEqual('BIOS image:   8ce05b02847603aef6cfa01f1bab73d0 '
                     '*test/image.bin\n',
                     self.packer._versions.getvalue())

  def testAddVersionInfo(self):
    """Check adding version info with both a filename and version."""
    self.packer._AddVersionInfo('BIOS', 'test/image.bin', 'v123')
    self.assertEqual('BIOS image:   8ce05b02847603aef6cfa01f1bab73d0 '
                     '*test/image.bin\nBIOS version: v123\n',
                     self.packer._versions.getvalue())

  def testExtractFrid(self):
    """Check extracting the firmware ID from a bios image."""
    self.packer._tmpdir = 'test'
    self.packer._testing = True
    self.packer._args = self.packer.ParseArgs(['--bios_image', 'image.bin'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap'), returncode=0)
      self.assertEqual(RO_FRID, self.packer._ExtractFrid('image.bin'))


if __name__ == '__main__':
  unittest.main()
