#!/usr/bin/env bash

# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# This script packages firmware images into an executale "shell-ball".
# It requires:
#  - at least one BIOS image and/or EC image (*.bin / *.fd)
#  - flashrom(8) as native binary tool
#  - pack_dist/install_firmware script to execute flashrom(8)
#  - pack_stub as the template/stub script for output
#  - any other additional files used by install_firmware in pack_dist folder

script_base="$(dirname "$0")"
. "$script_base/lib/shflags/shflags"

# DEFINE_string name default_value description flag
DEFINE_string bios_image "" "Path of input BIOS firmware image" "b"
DEFINE_string ec_image "" "Path of input EC firmware image" "e"
DEFINE_string output "-" "Path of output filename; '-' for stdout." "o"
DEFINE_string extra "" "Directory of extra files to be put in firmware package."

# tools
DEFINE_string flashrom "" \
  "Path of flashrom(8), using tool_base/flashrom if not assigned"
DEFINE_string iotools "" \
  "Path of iotools, using tool_base/iotools if not assigned"

# default tool location
DEFINE_string tool_base "" "Default location for tools (flashrom/iotools)"
DEFINE_string board "" "(deprecated) Alternative to tool_base param."

# Parse command line
FLAGS "$@" || exit 1
eval set -- "${FLAGS_ARGV}"

# backward-compatible
if [ -n "${FLAGS_board}" -a -z "${FLAGS_tool_base}" ]; then
  FLAGS_tool_base=/build/${FLAGS_board}/usr/sbin
fi

# provide default location of flashrom location
if [ "${FLAGS_flashrom}" == "" ]; then
  FLAGS_flashrom="${FLAGS_tool_base}/flashrom"
fi
if [ "${FLAGS_iotools}" == "" ]; then
  FLAGS_iotools="${FLAGS_tool_base}/iotools"
fi

# we need following tools to be inside a package:
#  - flashrom(8): native binary tool
#  - iotools: native binary tool
#  - pack_dist/install_firmware
#  - all files in pack_dist (usually used by install_firmware like selectors)

pack_dist="$script_base/pack_dist"
flashrom_bin="${FLAGS_flashrom}"
iotools_bin="${FLAGS_iotools}"
install_firmware_script="$pack_dist/install_firmware"
stub_file="$script_base/pack_stub"

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
type -P uuencode >/dev/null || err_die "ERROR: You need uuencode(sharutils)"

# check required basic files
for X in "$flashrom_bin" "$iotools_bin" \
         "$install_firmware_script" "$stub_file"; do
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

# copy other resources files from pack_dist
# XXX do not put any files with dot in prefix ( eg: .blah )
cp -p "$flashrom_bin" "$tmpbase"/flashrom || err_die "cannot copy tool flashrom"
cp -p "$iotools_bin" "$tmpbase"/iotools || err_die "cannot copy tool iotools"
cp -rp "$pack_dist"/* "$tmpbase" || err_die "cannot copy pack_dist folder"
chmod a+rx "$tmpbase"/flashrom "$tmpbase"/iotools "$tmpbase"/install_firmware

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
  (cat "$stub_file" &&
   tar zcf - -C "$tmpbase" . | uuencode firmware_package.tgz) ||
  err_die "ERROR: Failed to archive firmware package"
else
  # we can provide more information when output is not stdout
  (cat "$stub_file" &&
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
