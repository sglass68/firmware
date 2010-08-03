#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''Unittest wrapper for saft_utility.'''

import os
import subprocess
import sys
import tempfile
import unittest

import chromeos_interface
import saft_utility

ChrosIf = chromeos_interface.ChromeOSInterface(True)
base_partition = None

# List of devices the upstarts scripts need to be added to/removed from.
device_list = []


def get_device_list():
    dl = []
    root_dev = ChrosIf.run_shell_command_get_output('rootdev')[0]
    for line in ChrosIf.run_shell_command_get_output('blkid'):
        if (not 'LABEL="H-ROOT-' in line and
            not 'LABEL="C-KEYFOB' in line):
            continue
        dev = line.split()[0].strip(':')
        if dev == root_dev:
            dev = '/dev/root'
        dl.append(dev)
    return dl


def get_upstart_scripts():
    scripts = []
    tmp_dir = None

    all_mounts = ChrosIf.run_shell_command_get_output('mount')
    for dev in device_list:
        for mount in all_mounts:
            if mount.startswith(dev):
                mp = mount.split()[2]
                break
        else:
            tmp_dir = tempfile.mkdtemp()
            ChrosIf.run_shell_command('mount %s %s' % (dev, tmp_dir))
            mp = tmp_dir

        conf_file = mp + saft_utility.UPSTART_SCRIPT
        if not os.path.isfile(conf_file):
            continue
        f = open(conf_file)
        scripts.append(f.read())
        f.close()
        if tmp_dir:
            ChrosIf.run_shell_command('umount %s' % tmp_dir)
            os.rmdir(tmp_dir)
    return scripts


class TestUpstartHandler(unittest.TestCase):

    def setUp(self):
        self.fst = saft_utility.FirmwareTest()
        self.fst.init(sys.argv[0], ChrosIf, None)
        ChrosIf.init_environment()

    def test_upstart_handler(self):

    # The first invocation is supposed to create the fw_test.conf upstart
    # scripts in /etc/init on three partitions.
        self.fst._handle_upstart_script(True)

    # This returns the list of currently existing fw_test.conf instances.
        upstarts = get_upstart_scripts()
        self.assertEqual(len(upstarts), 3)

    # Verify that all instances are the same
        self.assertEqual(upstarts[0], upstarts[1])
        self.assertEqual(upstarts[0], upstarts[2])

    # This is supposed to delete all fw_test.conf instances.
        self.fst._handle_upstart_script(False)
        self.assertEqual(len(get_upstart_scripts()), 0)

    def tearDown(self):
        ChrosIf.shut_down()

if __name__ == '__main__':
    device_list = get_device_list()
    unittest.main()
