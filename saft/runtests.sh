#!/bin/sh
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# A simple script to run SAFT unit tests and possibly start the SAFT.
#
# The only expected optional command line parameter is the name of the file
# containing the firmware image to test. If the parameter is not provided,
# SAFT is not started, just the unit testst get to run.

rm -rf /tmp/tmp*/var/.fw_test 2> /dev/null
umount -d /tmp/tmp* 2> /dev/null

set -e
export PYTHONPATH=$(realpath ../x86-generic)
./test_chromeos_interface.py
./test_flashrom_handler.py /etc/keys/root_key.vbpubk $1
./test_saft_utility.py
./test_kernel_handler.py

if [ "$#" != "0" ]; then
  ./saft_utility.py --pub_ke=/etc/keys/root_key.vbpubk --ima=$1
fi
