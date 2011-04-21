#!/usr/bin/env bash

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# This script packages firmware images into an executale "shell-ball".
# It requires:
#  - at least one BIOS image and/or EC image (*.bin / *.fd)
#  - pack_dist/updater.sh main script
#  - pack_stub as the template/stub script for output
#  - any other additional files used by updater.sh in pack_dist folder

script_base="$(dirname "$0")"
SHFLAGS_FILE="$script_base/lib/shflags/shflags"
. "$SHFLAGS_FILE"

# DEFINE_string name default_value description flag
DEFINE_string bios_image "" "Path of input BIOS firmware image" "b"
DEFINE_string ec_image "" "Path of input EC firmware image" "e"
DEFINE_string ec_version "IGNORE" "Version of input EC firmware image"
DEFINE_string output "-" "Path of output filename; '-' for stdout" "o"
DEFINE_string extra "" "Directory list (separated by :) of files to be merged"

# embedded tools
# TODO(hungte) add crossystem after we've updated the ebuild files
DEFINE_string tools "flashrom mosys crossystem" \
  "List of tool programs to be bundled into updater"
DEFINE_string tool_base "" \
  "Default source locations for tools programs (delimited by colon)"

# deprecated parameters
DEFINE_string bios_version "(deprecated)" "Please don't use this."

# Parse command line
FLAGS "$@" || exit 1
eval set -- "${FLAGS_ARGV}"

# we need following tools to be inside a package:
#  - $FLAGS_tools: native binary tools
#  - pack_dist/updater.sh
#  - all files in pack_dist (usually used by updater.sh like selectors)

stub_file="$script_base/pack_stub"
pack_dist="$script_base/pack_dist"
main_script="$pack_dist/updater.sh"
tmpbase=""

# helper utilities

do_cleanup() {
  if [ -d "$tmpbase" ]; then
    rm -rf "$tmpbase"
    tmpbase=""
  fi
}

err_die() {
  if [ -n "$1" ]; then
    echo "ERROR: $1" >&2
  fi
  exit 1
}

has_command() {
  type "$1" >/dev/null 2>&1
}

find_tool() {
  local toolbase_list="$(echo "$FLAGS_tool_base" | tr ':' '\n')"
  local toolpath
  toolpath="$(echo "$toolbase_list" |
              while read toolbase; do
                if [ -f "$toolbase/$1" ]; then
                  echo "$toolbase/$1"
                  exit $FLAGS_TRUE
                fi
              done)"
  [ -n "$toolpath" ] && echo "$toolpath"
}

extract_frid() {
  local image_file="$(readlink -f "$1")"
  local default_frid="$2"
  local tmpdir="$(mktemp -d)"
  ( cd "$tmpdir"
    dump_fmap -x "$image_file" >/dev/null 2>&1 || true
    [ -s "RO_FRID" ] && cat "RO_FRID" || echo "$default_frid" )
  rm -rf "$tmpdir" 2>/dev/null
}

trap do_cleanup EXIT

# check tool: we need uuencode to create the shell ball.
has_command uuencode || err_die "You need uuencode(sharutils)"

# check required basic files
for X in "$main_script" "$stub_file"; do
  if [ ! -r "$X" ]; then
    err_die "Cannot find required file: $X"
  fi
done

# check tool programs
flashrom_bin=""
for X in $FLAGS_tools; do
  if ! find_tool "$X" >/dev/null; then
    err_die "Cannot find tool program to bundle: $X"
  fi
  if [ "$X" = "flashrom" ]; then
    flashrom_bin="$(find_tool $X)"
  fi
done

# check input: fail if no any images were assigned
bios_bin="${FLAGS_bios_image}"
bios_version="IGNORE"
ec_bin="${FLAGS_ec_image}"
ec_version="${FLAGS_ec_version}"
if [ "$bios_bin" = "" -a "$ec_bin" = "" ]; then
  err_die "must assign at least one of BIOS or EC image."
fi

# create temporary folder to store files
tmpbase="$(mktemp -d)" || err_die "Cannot create temporary folder."
version_file="$tmpbase/VERSION"
echo "Package create date: $(date +'%c')" >>"$version_file"
if [ -n "$flashrom_bin" ]; then
  flashrom_ver="$(
    strings "$flashrom_bin" |
    grep '^[0-9\.]\+ \+: \+[a-z0-9]\+ \+: \+.\+UTC')"
  echo "
flashrom(8): $(md5sum -b "$flashrom_bin")
             $(file -b "$flashrom_bin")
             $flashrom_ver" >>"$version_file"
fi
echo "" >>"$version_file"

# copy firmware image files
if [ "$bios_bin" != "" ]; then
  bios_version="$(extract_frid "$bios_bin" "IGNORE")"
  cp -pf "$bios_bin" "$tmpbase/bios.bin" || err_die "cannot get BIOS image"
  echo "BIOS image:   $(md5sum -b "$bios_bin")" >> "$version_file"
  [ "$bios_version" = "IGNORE" ] ||
    echo "BIOS version: $bios_version" >> "$version_file"
fi
if [ "$ec_bin" != "" ]; then
  ec_version="$(extract_frid "$ec_bin" "$FLAGS_ec_version")"
  cp -pf "$ec_bin" "$tmpbase/ec.bin" || err_die "cannot get EC image"
  echo "EC image:     $(md5sum -b "$ec_bin")" >> "$version_file"
  [ "$ec_version" = "IGNORE" ] ||
    echo "EC version:   $ec_version" >>"$version_file"
fi

# copy tool programs and main resources from pack_dist.
# WARNING: do not put any files with dot in prefix ( eg: .blah )
cp -pf "$SHFLAGS_FILE" "$tmpbase"/. || err_die "cannot copy shflags"
for X in $FLAGS_tools; do
  cp -pf "$(find_tool "$X")" "$tmpbase"/. || err_die "cannot copy $X"
  chmod a+rx "$tmpbase/$X"
done
cp -rfp "$pack_dist"/* "$tmpbase" || err_die "cannot copy pack_dist folder"

# adjust file permission
chmod a+rx "$tmpbase"/*.sh

# copy extra files. if $FLAGS_extra is a folder, copy all content inside.
extra_list="$(echo "${FLAGS_extra}" | tr ':' '\n')"
for extra in $extra_list; do
  if [ -d "$extra" ]; then
    cp -r "$extra"/* "$tmpbase" || \
      err_die "cannot copy extra files from folder $extra"
    echo "Extra files from folder: $extra" >> "$version_file"
  elif [ "$extra" != "" ]; then
    cp -r "$extra" "$tmpbase" || \
      err_die "Cannot copy extra files $extra"
    echo "Extra file: $extra" >> "$version_file"
  fi
done
chmod -R a+r "$tmpbase"/*

# create MD5 checksum logs
echo "
Package Content:" >> "$version_file"
(cd "$tmpbase" && find . -type f \! -name "VERSION" -exec md5sum -b {} \;) >> \
  "$version_file"

# package temporary folder into ouput file
# TODO(hungte) use 'shar' instead?
if [ -z "${FLAGS_output}" -o "${FLAGS_output}" = "-" ]; then
  output_opt=""
else
  output_opt="of=${FLAGS_output}"
fi
output="${FLAGS_output}"
(cat "$stub_file" |
 sed -e "s/REPLACE_FWVERSION/${bios_version}/" \
     -e "s/REPLACE_ECVERSION/${ec_version}/" &&
 tar zcf - -C "$tmpbase" . | uuencode firmware_package.tgz) |
 dd $output_opt 2>/dev/null || err_die "Failed to archive firmware package"

# we can provide more information when output is not stdout
if [ -n "$output_opt" ]; then
  chmod a+rx "$output"
  cat "$tmpbase/VERSION"
  echo ""
  echo "Packed output image is: $output"
fi
