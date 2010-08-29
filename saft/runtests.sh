#!/bin/bash
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# A simple script to run SAFT unit tests and possibly start the SAFT.
#
# The only expected optional command line parameter is the name of the file
# containing the firmware image to test. If the parameter is not provided,
# SAFT is not started, just the unit tests get to run.

rm -rf /tmp/tmp*/var/.fw_test 2> /dev/null
umount -d /tmp/tmp* 2> /dev/null

DEFAULT_FLASH_DEVICE="${FLASH_DEVICE:=sdb}"
set -e
this_prog=$(realpath $0)
if [ -n "$1" ]; then
    new_firmware=$(realpath $1)
else
    new_firmware=''
fi

on_removable_device() {
    # Check if the code is running off a removable device or not.
    # Print '1' or '0' respectively on the console.
    rootd=$(df "${this_prog}" | grep '^/dev' | awk '{ print $1 }')
    if [ "${rootd}" == '/dev/root' ]; then
        rootd=$(rootdev)
    fi
    blockd=$(echo "${rootd}"  | sed 's|.*/\([^/0-9]\+\)[0-9]\+|\1|')
    removable=$(cat /sys/block/"${blockd}"/removable)
    echo "${removable}"
}

move_to_removable_device() {
    # Move ourselves to the removable device and run off it.

    # For simplicity, hardcode the removable device to be /dev/sdb
    flash_device="${DEFAULT_FLASH_DEVICE}"
    fname="/sys/block/${flash_device}/removable"

    # Confirm SDB exists and is indeed removable.
    if [ ! -f  "${fname}" -o "$(cat $fname)" != "1" ]; then
        echo "No removable device found"
        exit 1
    fi

    # Determine userland partition on the removable device.
    dev_num=$(cgpt show /dev/"${flash_device}" | \
        grep '"ROOT-A"' | awk '{ print $3 }')
    flash_root_partition="/dev/${flash_device}${dev_num}"

    # Find its mountpoint, or mount it if not yet mounted.
    mp=$(mount | grep "${flash_root_partition}" | awk '{print $3}')
    if [ "${mp}" == "" ]; then
        mp=$(mktemp -d)
        mount "${flash_root_partition}" "${mp}"
    fi

    # Copy the two directories SAFT test requires to the removable device to
    # the same path we are in on the root device now.
    my_root=$(realpath ..)
    dest_root="${mp}${my_root}"
    for d in saft x86-generic; do
        dest="${dest_root}/$d"
        if [ ! -d "${dest}" ]; then
            mkdir -p "${dest}"
        fi
        cp -rp "${my_root}/${d}"/* "${dest}"
    done

    # Start it running off the removable device. We don't expect to come back
    # from this invocation in case this is a full mode SAFT. If this is a
    # unittest run, we some post processing can be added after unit tests
    # return.
    echo "starting as ${mp}${this_prog}  ${new_firmware}"
    "${mp}${this_prog}" "${new_firmware}"
}

configure_gpt_settings() {
    # Let's keep it simple for now, partition 2 is the one to boot.
    cgpt add -i 2 -T 5 -P 9 /dev/sda
    cgpt add -i 4 -T 5 -P 5 /dev/sda
}

run_tests() {
    export PYTHONPATH=$(realpath ../x86-generic)
    ./test_chromeos_interface.py
    ./test_flashrom_handler.py "${new_firmware}"
    ./test_saft_utility.py
    ./test_kernel_handler.py

    if [ -n "${new_firmware}" ]; then
        configure_gpt_settings
        ./saft_utility.py --ima="${new_firmware}"
    fi
}

cd $(dirname ${this_prog})
if [ "$(on_removable_device)" == "0" ]; then
    move_to_removable_device
    exit 0
fi

run_tests
