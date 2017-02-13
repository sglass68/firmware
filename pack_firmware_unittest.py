#!/usr/bin/env python
# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
from contextlib import contextmanager
from StringIO import StringIO
import sys
import unittest

import chromite.lib.cros_logging as logging
import pack_firmware
from pack_firmware import PackFirmware

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
    self.assertFalse(pack_firmware.main(['.']))
    self.assertTrue(pack_firmware.main(['.', '--script=updater5.sh']))

  def testBadStartup(self):
    """Starting up in another directory (without required files) should fail."""
    self.assertFalse(pack_firmware.main(['/']))

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

  def testHasCommand(self):
    self.assertTrue(self.pack._HasCommand('ls', 'sample-package'))
    self.assertFalse(self.pack._HasCommand('does-not-exist', 'sample-package'))

if __name__ == '__main__':
    unittest.main()
