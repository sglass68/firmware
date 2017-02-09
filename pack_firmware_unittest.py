# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for pack_firmware.py.

This mocks out all tools so it can run fairly quickly.
"""

from __future__ import print_function

from contextlib import contextmanager
import sys
import unittest

try:
  from StringIO import StringIO
except ImportError:
  from io import StringIO

import chromite.lib.cros_logging as logging
from pack_firmware import FirmwarePacker

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
  """Test cases for common program flows."""
  def setUp(self):
    self.pack = FirmwarePacker('.')

  def testArgParse(self):
    """Test some basic argument parsing as a sanity check."""
    with self.assertRaises(SystemExit):
      with capture_sys_output():
        self.assertEqual(None, self.pack.ParseArgs(['--invalid']))

    self.assertEqual(None, self.pack.ParseArgs([]).bios_image)
    self.assertEqual('bios.bin',
                     self.pack.ParseArgs(['-b', 'bios.bin']).bios_image)

    self.assertEqual(True, self.pack.ParseArgs([]).remove_inactive_updaters)
    self.assertEqual(False,
                     self.pack.ParseArgs(['--no-remove_inactive_updaters'])
                     .remove_inactive_updaters)

    self.assertEqual(True, self.pack.ParseArgs([]).merge_bios_rw_image)
    self.assertEqual(True, self.pack.ParseArgs(['--merge_bios_rw_image'])
                     .merge_bios_rw_image)
    self.assertEqual(False, self.pack.ParseArgs(['--no-merge_bios_rw_image'])
                     .merge_bios_rw_image)


if __name__ == '__main__':
  unittest.main()
