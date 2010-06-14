#!/bin/bash

# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.# Use of this
# source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Load common constants.  This should be the first executable line.
# The path to common.sh should be relative to your script's location.
. "$(dirname "$0")/../../../scripts/common.sh"

DEFINE_string to "-" "Path of the output image; if \\\"-\\\", meaning stdout."

# Parse command line
FLAGS "$@" || exit 1
eval set -- "${FLAGS_ARGV}"

# List the files need to be packed.
PACK_FILES="system_rom.bin ec_rom.bin"

for file in ${PACK_FILES}; do
  if [ ! -e ${file} ]; then
    echo "File ${file} does not exist." >&2
    exit 1
  fi
done

TMP_FILE="/tmp/firmware"
cp -f install_firmware.sh "${TMP_FILE}"
tar zcO ${PACK_FILES} | uuencode packed_files.tgz >> "${TMP_FILE}"

if [ ${FLAGS_to} = "-" ]; then
  cat "${TMP_FILE}"
  rm -f "${TMP_FILE}"
else
  mv "${TMP_FILE}" "${FLAGS_to}"
  echo "Packed output image is: ${FLAGS_to}"
fi
