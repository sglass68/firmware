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


def saft_script_present():
    '''Check if SAFT shell script is present on the flash stateful partition.'''

    tmp_dir = None
    script_found = False
    for line in ChrosIf.run_shell_command_get_output('blkid'):
        # Find flash stateful partition device using its label.
        if not 'LABEL="C-STATE"' in line:
            continue
        dev = line.split()[0].strip(':')

        # Get its mount point, if it is mounted.
        all_mounts = ChrosIf.run_shell_command_get_output('mount')
        for mount in all_mounts:
            if mount.startswith('%s ' % dev):
                mount_point = mount.split()[2]
                break
        else:
            # It is not mounted, mount it.
            tmp_dir = tempfile.mkdtemp()
            ChrosIf.run_shell_command('mount %s %s' % (dev, tmp_dir))
            mount_point = tmp_dir

        conf_file = mount_point + saft_utility.RetriveSaftConfDefinion(
            'SAFT_SCRIPT')

        script_found = os.path.isfile(conf_file)
        if tmp_dir:
            ChrosIf.run_shell_command('umount %s' % tmp_dir)
            os.rmdir(tmp_dir)

    return script_found


class TestUpstartHandler(unittest.TestCase):

    def setUp(self):
        self.fst = saft_utility.FirmwareTest()
        self.fst.init(sys.argv[0], ChrosIf, None)
        ChrosIf.init_environment()

    def test_saft_script_handler(self):

        # The first invocation is supposed to create the saft shell script in
        # /var on the flash drive.
        self.fst._handle_saft_script(True)
        self.assertTrue(saft_script_present())

        # This is supposed to delete the script
        self.fst._handle_saft_script(False)
        self.assertFalse(saft_script_present())

    def test_file_name_retrieval(self):

        # these two should go smoothly.
        for name in ('SAFT_LOG', 'SAFT_SCRIPT'):
            saft_utility.RetriveSaftConfDefinion(name)

        self.assertRaises(saft_utility.FwError,
                          saft_utility.RetriveSaftConfDefinion,
                          'dummy_name')

    def tearDown(self):
        ChrosIf.shut_down()

if __name__ == '__main__':
    unittest.main()
