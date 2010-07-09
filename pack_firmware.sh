#!/usr/bin/env bash

# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# This script packages firmware images into an executale "shell-ball".
# It requires:
#  - at least one BIOS image and/or EC image (*.bin / *.fd)
#  - flashrom(8) as native binary tool
#  - $BOARD/install_firmware script to execute flashrom(8)
#  - shellball.sh.template as the stub script template for output
#  - any other additional files used by install_firmware in $BOARD folder

# Load common constants.  This should be the first executable line.
# The path to common.sh should be relative to your script's location.
script_base="$(dirname "$0")"
. "$script_base/../../scripts/common.sh"

# Script must be run inside the chroot
restart_in_chroot_if_needed $*

get_default_board

# DEFINE_string name default_value description flag
DEFINE_string bios_image "" "Path of input BIOS firmware image" "b"
DEFINE_string ec_image "" "Path of input EC firmware image" "e"
DEFINE_string output "-" "Path of output filename; '-' for stdout." "o"
DEFINE_string board "$DEFAULT_BOARD" "The board to build packages for."
DEFINE_string extra "" "Directory of extra files to be put in firmware package."

DEFINE_string flashrom "" \
  "Path of flashrom(8), using /build/[board]/usr/sbin/flashrom if not assigned"
DEFINE_string iotools "" \
  "Path of iotools, using /build/[board]/usr/sbin/iotools if not assigned"

# Parse command line
FLAGS "$@" || exit 1
eval set -- "${FLAGS_ARGV}"

# provide default location of flashrom location
if [ "${FLAGS_flashrom}" == "" ]; then
  FLAGS_flashrom="/build/${FLAGS_board}/usr/sbin/flashrom"
fi
if [ "${FLAGS_iotools}" == "" ]; then
  FLAGS_iotools="/build/${FLAGS_board}/usr/sbin/iotools"
fi

# we need following tools to be inside a package:
#  - flashrom(8): native binary tool
#  - iotools: native binary tool
#  - $BOARD/install_firmware
#  - all files in $BOARD (usually used by install_firmware like selectors)

board_base="$script_base/${FLAGS_board}"
if [ ! -d "$board_base" ] && \
   [ -d "$script_base/${FLAGS_board/-*/-generic}" ]; then
   board_base="${script_base}/${FLAGS_board/-*/-generic}"
   # echo "FLAGS_board changed to: ${board_base}"
fi

flashrom_bin="${FLAGS_flashrom}"
iotools_bin="${FLAGS_iotools}"
install_firmware_script="$board_base/install_firmware"
template_file="$script_base/shellball.sh.template"

function do_cleanup {
  if [ -d "$tmpbase" ]; then
    rm -rf "$tmpbase"
  fi
}

function err_die {
  if [ "$1" != "" ]; then
    echo "$1" >&2
  fi
  do_cleanup
  exit 1
}

# check tool: we need uuencode to create the shell ball.
which uuencode >/dev/null || err_die "ERROR: You need uuencode(sharutils)"

# check required basic files
for X in "$flashrom_bin" "$iotools_bin" \
         "$install_firmware_script" "$template_file"; do
  if [ ! -r "$X" ]; then
    err_die "ERROR: Cannot find required file: $X"
  fi
done

# check input: fail if no any images were assigned
bios_bin="${FLAGS_bios_image}"
ec_bin="${FLAGS_ec_image}"
if [ "$bios_bin" == "" -a "$ec_bin" == "" ]; then
  err_die "ERROR: must assign at least one of BIOS or EC image."
fi

# create temporary folder to store files
tmpfolder="`mktemp -d`" || err_die "Cannot create temporary folder."

# XXX only define 'tmpbase' after we've successfully created it because err_die
# will destroy $tmpbase.
tmpbase="$tmpfolder"
version_file="$tmpbase/VERSION"
echo "Package create date: `date +'%c'`

Board:       ${FLAGS_board}
iotools:     $(md5sum -b "$iotools_bin")
             $(file -b "$iotools_bin")
flashrom(8): $(md5sum -b "$flashrom_bin")
             $(file -b "$flashrom_bin")" >> \
  "$version_file"

# copy firmware image files
if [ "$bios_bin" != "" ]; then
  cp "$bios_bin" "$tmpbase/bios.bin" || err_die "cannot get BIOS image"
  echo "BIOS image:  $(md5sum -b "$bios_bin")" >> "$version_file"
fi
if [ "$ec_bin" != "" ]; then
  cp "$ec_bin" "$tmpbase/ec.bin" || err_die "cannot get EC image"
  echo "EC image:    $(md5sum -b "$ec_bin")" >> "$version_file"
fi

# copy other resources files from $board
# XXX do not put any files with dot in prefix ( eg: .blah )
cp "$flashrom_bin" "$tmpbase"/flashrom || err_die "cannot copy tool flashrom(8)"
cp "$iotools_bin" "$tmpbase"/iotools || err_die "cannot copy tool iotools"
cp -r "$board_base"/* "$tmpbase" || err_die "cannot copy board folder"

# copy extra files. if $FLAGS_extra is a folder, copy all content inside.
if [ -d "${FLAGS_extra}" ]; then
  cp -r "${FLAGS_extra}"/* "$tmpbase" || \
    err_die "cannot copy extra files from folder ${FLAGS_extra}"
  echo "Extra files from folder: ${FLAGS_extra}" >> "$version_file"
elif [ "${FLAGS_extra}" != "" ]; then
  cp -r "${FLAGS_extra}" "$tmpbase" || \
    err_die "cannot copy extra files ${FLAGS_extra}"
  echo "Extra file: ${FLAGS_extra}" >> "$version_file"
fi

# create MD5 checksum logs
echo "
Package Content:" >> "$version_file"
(cd "$tmpbase" && find . -type f \! -name "VERSION" -exec md5sum -b {} \;) >> \
  "$version_file"

# package temporary folder into ouput file
# TODO(hungte) use 'shar' instead?
output="${FLAGS_output}"
if [ "$output" == "-" ]; then
  (cat "$template_file" &&
   tar zcf - -C "$tmpbase" . | uuencode firmware_package.tgz) ||
  err_die "ERROR: Failed to archive firmware package"
else
  # we can provide more information when output is not stdout
  (cat "$template_file" &&
   tar zcf - -C "$tmpbase" . | uuencode firmware_package.tgz) > "$output" ||
   err_die "ERROR: Failed to archive firmware package"
  chmod a+rx "$output"
  cat "$tmpbase/VERSION"
  echo ""
  echo "Packed output image is: $output"
fi

# clean resources
do_cleanup
exit 0
