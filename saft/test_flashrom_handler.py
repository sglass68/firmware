#!/usr/bin/env python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''Unittest for flashrom_handler module.

This script can be run in a host, chroot or target environment. It requires
the vbutil_firmware utility to be available in the path.

When run as a program from the command line, this script expects one or two
parameters. The first parameter is the file name of the public key which can
be used to verify the firmware image. The second parameter, when specified, is
the file name of a ChromeOS firmware file. If the script is run in the target
environment and the firmaware file name is not specified, the current firmware
is read using 'flashrom -r' command.

See usage() below for command line options.
'''

import os
import shutil
import sys
import tempfile
import unittest

import flashrom_handler
import flashrom_util

progname = os.path.basename(sys.argv[0])
pub_key_file = None
test_fd_file = None


def usage():
  text = '''
usage: %s <key_file> [<fd_image_file>]
    <fd_image_file> can be omitted on the target, in which case
           it is read from the flashrom.
   NOTE: vbutil_firmware utility must be available in PATH.
'''
  print >> sys.stderr, (text % progname).strip()
  sys.exit(1)

class TestFlashromHandler(unittest.TestCase):
  def setUp(self):
    self.tmpd = tempfile.mkdtemp()
    self.fh = flashrom_handler.FlashromHandler()
    self.fh.init(flashrom_util, self.tmpd, pub_key_file)

  def test_image_read(self):
    self.fh.new_image(test_fd_file)
    self.fh.verify_image()

  def test_image_corrupt_restore(self):
    image_name = os.path.join(self.tmpd, 'tmp.image')
    self.fh.new_image(test_fd_file)
    self.fh.verify_image()
    self.fh.dump_whole(image_name)
    self.fh.new_image(image_name)
    self.fh.verify_image()
    for section in ('a', 'b'):
      corrupted_file_name = image_name + section
      corrupted_subsection = self.fh.corrupt_section(section)
      self.fh.dump_whole(corrupted_file_name)
      self.fh.new_image(corrupted_file_name)
      try:
        self.fh.verify_image()
      except flashrom_handler.FlashromHandlerError, e:
        self.assertEqual(e[0], 'Failed verifying ' + corrupted_subsection)

      self.fh.restore_section(section)
      self.fh.dump_whole(corrupted_file_name)
      self.fh.new_image(corrupted_file_name)
      self.fh.verify_image()
      self.fh.new_image(image_name)

  def tearDown(self):
    shutil.rmtree(self.tmpd)

if __name__ == '__main__':
  if len(sys.argv) < 2:
    usage()
  pub_key_file = sys.argv[1]
  if len(sys.argv) > 2:
    test_fd_file = sys.argv[2]
  sys.argv = sys.argv[0:1]
  sys.path.append('../x86-generic')
  unittest.main()
