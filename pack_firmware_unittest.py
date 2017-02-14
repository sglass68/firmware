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
import pack_firmware
from pack_firmware import PackFirmware, PackError

# Disable all logging as it's confusing to get log output from tests.
logging.getLogger().setLevel(logging.CRITICAL + 1)


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

  def testStartup(self):
    """Starting up with a valid updater script should work."""
    args = ['.', '--script=updater5.sh', '--tools', 'ls',
            '--tool_base', '/bin:.', '-b', 'image.bin']
    pack_firmware.main(args)

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
    self.pack._tmpdir = '.'
    with open('RO_FRID', 'w') as fd:
      fd.write('1234')
    self.pack._args = self.pack.ParseArgs(['--bios_image', 'image.bin'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap'), returncode=0)
      self.assertEqual('1234', self.pack._ExtractFrid('image.bin'))

if __name__ == '__main__':
    unittest.main()
