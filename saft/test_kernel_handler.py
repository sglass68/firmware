#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''Unit test for kernel_handler module.

Allows to verify kernel corrupt and restore actions.
'''

import shutil
import struct
import sys
import unittest

import chromeos_interface
import kernel_handler
import flashrom_util

# Base offset where public key data starts in the VbKeyBlockHeader structure.
KEYBLOCK_SIZE_ACCESS_FORMAT = '<16sQ'
FW_PREAMBLE_PUBKEY_OFFSET = 48
PUBKEY_HEADER_FORMAT = '<QQQQ'
PUBKEY_FILE = 'kernel.vbpubk'
KERNEL_FILE = 'kernel'
BACKUP_FILE = 'kernel.backup'
MAIN_ROOT_DEV = '/dev/sda'
SECTIONS = ('A', 'B')
BACKUP_CMD = 'dd if=%s of=%s bs=1000000 count=1'

def get_kernel_key(section, pubkey_file):
    '''Retrieve from firmware the public key used to verify the kernel.

    Skip the keyblock to reach the firmware preamble. Find out where the
    public key is and extract it into a separate file, adjusting the header's
    'offset' value.
    '''
    fum = flashrom_util.flashrom_util()
    image = fum.read_whole()
    # get the block header
    kblock = fum.get_section(image, 'VBOOT' + section)
    kbsize = struct.unpack_from(KEYBLOCK_SIZE_ACCESS_FORMAT, kblock)[1]

    pk_base_offset = kbsize + FW_PREAMBLE_PUBKEY_OFFSET
    offset, size, alg, version = struct.unpack_from(
        PUBKEY_HEADER_FORMAT, kblock[pk_base_offset:])

    key_data_offset = pk_base_offset + offset

    # Retrieve the public key and save it in a file.
    pub_key = struct.pack('<QQQQ%ds' % size, 32, size, alg, version,
                          kblock[key_data_offset:key_data_offset + size])

    keyf = open(pubkey_file, 'w')
    keyf.write(pub_key)
    keyf.close()

class TestKernelHandler(unittest.TestCase):
    '''Unit test for kernel handler.'''

    def setUp(self):
        '''Prepare a unit test run.

        Initialize objects needed to support testing, redirect stdout to avoid
        garbage printed on the console while the test is running.
        '''
        self.chros_if = chromeos_interface.ChromeOSInterface(True)
        self.chros_if.init()
        self.tmpd = self.chros_if.init_environment()
        self.kernel_handler = kernel_handler.KernelHandler()
        self.kernel_handler.init(self.chros_if)
        self.kernel_file = self.chros_if.state_dir_file(KERNEL_FILE)
        self.backup_file = None
        self.device = None
        self.stdout = sys.stdout
        sys.stdout = open('/dev/null', 'w')

    def set_kernel_device(self, section):
        '''Find out device storing a particular kernel.

        section - a single character string, A or B, designating the kernel in
                  question.

        'cgpt show MAIN_ROOT_DEV` output is scanned for the kernel partition
        names, then the device number is retrieved from the same line.
        '''

        pattern = 'Label: "KERN-' + section
        for line in self.chros_if.run_shell_command_get_output(
            'cgpt show %s' % MAIN_ROOT_DEV):
            if pattern in line:
                self.device = MAIN_ROOT_DEV + line.split()[2]
                return
        self.assertTrue(False)  # Failed to get the kernel.

    def back_up_kernel(self):
        '''Preserve the first megabyte of the kernel.

        Setting self.backup_file signifies that the kernel was backed up and
        needs to be restored if the test fails somewhere along the way.
        '''
        backup_file = self.chros_if.state_dir_file(BACKUP_FILE)
        self.chros_if.run_shell_command(
            BACKUP_CMD % (self.device, backup_file))
        self.backup_file = backup_file

    def test_corrupt_kernel(self):
        '''Iterate through kernels corrupting and restoring them.

        Confirm that verification fails after a kernel was corrupted and
        succeeds after the kernel was restored.
        '''
        pubkey_file = self.chros_if.state_dir_file(PUBKEY_FILE)
        verify_cmd = 'vbutil_kernel --verify %s --signpubkey %s' % (
            self.kernel_file, pubkey_file)
        for section in SECTIONS:
            get_kernel_key(section, pubkey_file)
            self.set_kernel_device(section)
            get_kernel_cmd = 'dd if=%s of=%s' % (self.device, self.kernel_file)
            self.back_up_kernel()
            self.kernel_handler.corrupt_kernel(section)
            self.chros_if.run_shell_command(get_kernel_cmd)
            self.assertRaises(chromeos_interface.ChromeOSInterfaceError,
                              self.chros_if.run_shell_command, verify_cmd)
            self.kernel_handler.restore_kernel(section)
            self.chros_if.run_shell_command(get_kernel_cmd)
            self.chros_if.run_shell_command(verify_cmd)
            self.backup_file = None

    def tearDown(self):
        '''Clean up after the test.
        In case the backup file is still defined (which means that a kernel
        was not restored properly) restore the kernel.
        Remove temporary directories.
        '''
        sys.stdout = self.stdout
        if self.backup_file:
            self.chros_if.run_shell_command(
                BACKUP_CMD % (self.backup_file, self.device))
            self.backup_file = None
        shutil.rmtree(self.tmpd)


if __name__ == '__main__':
    unittest.main()




