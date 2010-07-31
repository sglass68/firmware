#!/usr/bin/env python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''Unittest wrapper for saft_utility.'''

import os
import subprocess
import sys
import tempfile
import unittest

base_partition = None

# Create a symlink to a *.py name to make it possible to import the main
# module being tested.
os.symlink('saft_utility', 'saft_utility.py')
import saft_utility
os.remove('saft_utility.py')

# List of devices the upstarts scripts need to be added to/removed from.
device_list = []

def get_device_list():
  dl = []
  root_dev = saft_utility.run_shell_command_get_output('rootdev')[0]
  for line in saft_utility.run_shell_command_get_output('blkid'):
    if not 'LABEL="H-ROOT-' in line and not 'LABEL="C-KEYFOB' in line:
      continue
    dev = line.split()[0].strip(':')
    if dev == root_dev:
      dev = '/dev/root'
    dl.append(dev)
  return dl

def get_upstart_scripts():
  scripts = []
  tmp_dir = None

  all_mounts = saft_utility.run_shell_command_get_output('mount')
  for dev in device_list:
    for mount in all_mounts:
      if mount.startswith(dev):
        mp = mount.split()[2]
        break
    else:
      tmp_dir = tempfile.mkdtemp()
      saft_utility.run_shell_command('mount %s %s' % (dev, tmp_dir))
      mp = tmp_dir

    conf_file = mp + saft_utility.UPSTART_SCRIPT
    if not os.path.isfile(conf_file):
      continue
    f = open(conf_file)
    scripts.append(f.read())
    f.close()
    if tmp_dir:
      saft_utility.run_shell_command('umount %s' % tmp_dir)
      os.rmdir(tmp_dir)
  return scripts

class TestShellCommands(unittest.TestCase):
  TEST_CMD = 'ls /etc/init'
  def setUp(self):
    self.expected_text = subprocess.Popen(
        self.TEST_CMD, shell=True, stdout=subprocess.PIPE).stdout.read().strip()

  def test_run_shell_command(self):
    p = saft_utility.run_shell_command(self.TEST_CMD)
    self.assertEqual(p.stdout.read().strip(), self.expected_text)

  def test_run_shell_command_get_output(self):
    o = saft_utility.run_shell_command_get_output(self.TEST_CMD)
    self.assertEqual(o, self.expected_text.split('\n'))

class TestUpstartHandler(unittest.TestCase):
  def test_upstart_handler(self):

    # The first invocation is supposed to create the fw_test.conf upstart
    # scripts in /etc/init on three partitions.
    saft_utility.handle_upstart_script(base_partition, True)

    # This returns the list of currently existing fw_test.conf instances.
    upstarts = get_upstart_scripts()
    self.assertEqual(len(upstarts), 3)

    # Verify that all instances are the same
    self.assertEqual(upstarts[0], upstarts[1])
    self.assertEqual(upstarts[0], upstarts[2])

    # This is supposed to delete all fw_test.conf instances.
    saft_utility.handle_upstart_script(base_partition, False)
    self.assertEqual(len(get_upstart_scripts()), 0)


if __name__ == '__main__':
  pipe = subprocess.Popen('df %s' % sys.argv[0], shell=True,
                          stdout=subprocess.PIPE).stdout
  base_partition = pipe.readlines()[-1].split()[0]
  device_list = get_device_list()
  unittest.main()
