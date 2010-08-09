#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''A module containing kernel handler class used by SAFT.'''

import re

TMP_FILE_NAME = 'kernel_header_dump'
MAIN_STORAGE_DEVICE = '/dev/sda'

class KernelHandler(object):
    '''An object to provide ChromeOS kernel related actions.

    Mostly it allows to corrupt and restore a particular kernel partition
    (designated by the partition name, A or B.
    '''

    # This value is used to alter contents of a byte in the appropriate kernel
    # image. First added to corrupt the image, then subtracted to restore the
    # image.
    DELTA = 1
    def __init__(self):
        self.chros_if = None
        self.dump_file_name = None
        self.partition_map = {}

    def _get_partition_map(self):
        '''Scan `cgpt show <device> output to find kernel devices.'''
        kernel_partitions = re.compile('KERN-([AB])')
        disk_map = self.chros_if.run_shell_command_get_output(
            'cgpt show %s' % MAIN_STORAGE_DEVICE)

        for line in disk_map:
            matched_line = kernel_partitions.search(line)
            if not matched_line:
                continue
            label = matched_line.group(1)
            device = MAIN_STORAGE_DEVICE + line.split()[2]
            self.partition_map[label] = device

    def _modify_kernel(self, section, delta):
        '''Modify kernel image on a disk partition.

        Presently all this method does is adding the value of delta to the
        first byte of the kernel partition. This will have to be enhanced to
        make it possible to corrupt the kernel image in a more sophisticated
        way.
        '''
        dev = self.partition_map[section]
        cmd_template = 'dd if=%s of=%s bs=1 count=1'
        self.chros_if.run_shell_command(cmd_template % (
                dev, self.dump_file_name))
        bfile = open(self.dump_file_name, 'r')
        data = list(bfile.read())
        bfile.close()
        data[0] = '%c' % ((ord(data[0]) + delta) % 0x100)
        dumpf = open(self.dump_file_name, 'w')
        dumpf.write(''.join(data))
        dumpf.close()
        self.chros_if.run_shell_command(cmd_template % (
                self.dump_file_name, dev))

    def corrupt_kernel(self, section):
        self._modify_kernel(section.upper(), self.DELTA)

    def restore_kernel(self, section):
        self._modify_kernel(section.upper(), -self.DELTA)

    def init (self, chros_if):
        '''Initialize the kernel handler object.

        Input argument is a ChromeOS interface object reference.
        '''
        self.chros_if = chros_if
        self.dump_file_name = chros_if.state_dir_file(TMP_FILE_NAME)
        self._get_partition_map()
