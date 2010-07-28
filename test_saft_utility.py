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

def GetDeviceList():
  dl = []
  root_dev = saft_utility.RunShellCommandGetOutput('rootdev')[0]
  for line in saft_utility.RunShellCommandGetOutput('blkid'):
    if not 'LABEL="H-ROOT-' in line and not 'LABEL="C-KEYFOB' in line:
      continue
    dev = line.split()[0].strip(':')
    if dev == root_dev:
      dev = '/dev/root'
    dl.append(dev)
  return dl

def GetUpstartScripts():
  scripts = []
  tmp_dir = None

  all_mounts = saft_utility.RunShellCommandGetOutput('mount')
  for dev in device_list:
    for mount in all_mounts:
      if mount.startswith(dev):
        mp = mount.split()[2]
        break
    else:
      tmp_dir = tempfile.mkdtemp()
      saft_utility.RunShellCommand('mount %s %s' % (dev, tmp_dir))
      mp = tmp_dir

    conf_file = mp + saft_utility.UPSTART_SCRIPT
    if not os.path.isfile(conf_file):
      continue
    f = open(conf_file)
    scripts.append(f.read())
    f.close()
    if tmp_dir:
      saft_utility.RunShellCommand('umount %s' % tmp_dir)
      os.rmdir(tmp_dir)
  return scripts

class TestShellCommands(unittest.TestCase):
  TEST_CMD = 'ls /etc/init'
  def setUp(self):
    self.expected_text = subprocess.Popen(
        self.TEST_CMD, shell=True, stdout=subprocess.PIPE).stdout.read().strip()

  def test_RunShellCommand(self):
    p = saft_utility.RunShellCommand(self.TEST_CMD)
    self.assertEqual(p.stdout.read().strip(), self.expected_text)

  def test_RunShellCommandGetOutput(self):
    o = saft_utility.RunShellCommandGetOutput(self.TEST_CMD)
    self.assertEqual(o, self.expected_text.split('\n'))

class TestUpstartHandler(unittest.TestCase):
  def test_UpstartHandler(self):

    # The first invocation is supposed to create the fw_test.conf upstart
    # scripts in /etc/init on three partitions.
    saft_utility.HandleUpstartScript(base_partition, True)

    # This returns the list of currently existing fw_test.conf instances.
    upstarts = GetUpstartScripts()
    self.assertEqual(len(upstarts), 3)

    # Verify that all instances are the same
    self.assertEqual(upstarts[0], upstarts[1])
    self.assertEqual(upstarts[0], upstarts[2])

    # This is supposed to delete all fw_test.conf instances.
    saft_utility.HandleUpstartScript(base_partition, False)
    self.assertEqual(len(GetUpstartScripts()), 0)


if __name__ == '__main__':
  pipe = subprocess.Popen('df %s' % sys.argv[0], shell=True,
                          stdout=subprocess.PIPE).stdout
  base_partition = pipe.readlines()[-1].split()[0]
  device_list = GetDeviceList()
  unittest.main()
