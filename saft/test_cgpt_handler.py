#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import sys
import unittest

import cgpt_handler

MOCKED_COMMANDS = {
            'cgpt show /dev/sda' : '''
     start      size    part  contents
         0         1          PMBR (Boot GUID: F04C2365-F057-A54B-950B)
         1         1          Pri GPT header
         2        32          Pri GPT table
    266240  26816512       1  Label: "STATE"
                              Type: Linux data
                              UUID: F51243D8-53D5-3847-BA82-99B059AF0CCD
      4096     32768       2  Label: "KERN-A"
                              Type: ChromeOS kernel
                              UUID: 100A2338-83C5-D942-A565-12596B08EC12
                              Attr: priority=5 tries=0 successful=0
  29179904   2097152       3  Label: "ROOT-A"
                              Type: ChromeOS rootfs
                              UUID: 14284FB6-CB57-F44C-BFD2-A41677D1574F
     36864     32768       4  Label: "KERN-B"
                              Type: ChromeOS kernel
                              UUID: 46B99067-370D-E24E-BF4E-1054657D23A9
                              Attr: priority=7 tries=0 successful=1
  27082752   2097152       5  Label: "ROOT-B"
                              Type: ChromeOS rootfs
                              UUID: 6C13ED90-B312-E443-A9C9-0AA36EC16674
        34         1       6  Label: "KERN-C"
                              Type: ChromeOS kernel
                              UUID: 1319D204-0482-B844-8A01-B673888E6BC4
                              Attr: priority=0 tries=15 successful=0
        35         1       7  Label: "ROOT-C"
                              Type: ChromeOS rootfs
                              UUID: 500D4D29-3D72-4349-8F95-C9DC842D67E7
     69632     32768       8  Label: "OEM"
                              Type: Linux data
                              UUID: C6D00333-ACC5-6146-BC12-40D93CD7BDDF
        36         1       9  Label: "reserved"
                              Type: ChromeOS reserved
                              UUID: E579F1BF-F936-5A41-833F-E33B75991999
        37         1      10  Label: "reserved"
                              Type: ChromeOS reserved
                              UUID: 0C246088-140D-344C-8034-6210F2E1DBFD
        38         1      11  Label: "reserved"
                              Type: ChromeOS reserved
                              UUID: 6ABB61AA-C563-F745-AA84-36DEE1487039
    233472     32768      12  Label: "EFI-SYSTEM"
                              Type: EFI System Partition
                              UUID: F04C2365-F057-A54B-950B-9D335F2FD2E4
  31277199        32          Sec GPT table
  31277231         1          Sec GPT header
'''
            }
class MockChrosIf(object):
    def __init__(self):
        self.last_command = ''

    def run_shell_command_get_output(self, command):
        self.last_command = command
        return [x.rstrip() for x in MOCKED_COMMANDS[command].split('\n')]

    def run_shell_command(self, command):
        self.last_command = command

    def clear_last_command(self):
        self.last_command = ''

    def get_last_command(self):
        return self.last_command


class TestCgptHandler(unittest.TestCase):
    def setUp(self):
        self.device = '/dev/sda'
        self.chros_if = MockChrosIf()
        self.ch = cgpt_handler.CgptHandler(self.chros_if)
        self.ch.read_device_info(self.device)

    def test_device_parser(self):
        self.assertTrue(len(self.ch.devices) == 1)
        self.assertTrue(len(self.ch.devices[self.device]) == 10)

    def test_partition_access(self):
        self.ch.get_partition(self.device, 'STATE')
        self.assertRaises(cgpt_handler.CgptError, self.ch.get_partition,
                          self.device, 'what?')

    def test_set_partition(self):
        partition = { 'successful': 1 }
        self.chros_if.clear_last_command()
        self.ch.set_partition('/dev/sda', 'KERN-A', partition)
        self.assertEqual(self.chros_if.get_last_command(),
                         'cgpt add -i 2 -S 1 /dev/sda')

        self.chros_if.clear_last_command()
        self.ch.set_partition(self.device, 'KERN-B', partition)
        self.assertEqual(self.chros_if.get_last_command(), '')

        partition = {'dummy': 'xyz'}
        self.assertRaises(cgpt_handler.CgptError, self.ch.set_partition,
                          self.device, 'KERN-B', partition)

    def test_dump_partition(self):
        text = self.ch.dump_partition('/dev/sda', 'KERN-B').split('\n')
        expected = ['Type: ChromeOS kernel', 'partition: 4',
                    'UUID: 46B99067-370D-E24E-BF4E-1054657D23A9',
                    'priority: 7', 'tries: 0', 'successful: 1']
        for line in text:
            self.assertTrue(line in expected)

        self.assertEqual(len(text), len(expected))

if __name__ == '__main__':
    unittest.main()
