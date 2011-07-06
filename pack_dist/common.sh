#!/bin/sh
#
# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

SCRIPT_BASE="$(dirname "$0")"
if [ -s "$SCRIPT_BASE/shflags" ]; then
  . "$SCRIPT_BASE/shflags"
else
  . /usr/lib/shflags
fi
. "$SCRIPT_BASE/crosutil.sh"

# ----------------------------------------------------------------------------
# Common Utilities

# Reports some critical message in stderr
alert() {
  echo "$*" 1>&2
}

# Reports error message and exit(1)
# NOTE: top level script does not exit if call to err_die is inside a
# sub-shell (ex: (func-call), $(func-call)) or if-blocks.
err_die() {
  alert "ERROR: $*"
  exit 1
}

# Alias for err_die.
die() {
  err_die "$@"
}

# Reports error message and exit(2)
# NOTE: top level script does not exit if call to err_die is inside a
# sub-shell (ex: (func-call), $(func-call)) or if-blocks.
err_die_need_reboot() {
  alert "ERROR: $*"
  exit 3
}

# Prints a message if in verbose mode
verbose_msg() {
  if [ "$FLAGS_verbose" = "$FLAGS_TRUE" ] ||
     [ "$FLAGS_debug" = "$FLAGS_TRUE" ]; then
    echo "$*"
  fi
}

# Prints a message if in debug mode
debug_msg() {
  if [ "$FLAGS_debug" = "$FLAGS_TRUE" ]; then
    alert " (DEBUG) $*"
  fi
}

# Helps functions to check if there were given parameters in correct form.
# Syntax: check_param "prototype" "$@"
#         prototype is a string in "funcname(arg1,arg2,arg3)" form.
check_param() {
  local original_format="$1"
  local format="$(echo "$1" | sed 's/[(,) ]/ /g; s/  */ /g;')"
  local has_wild_arg=0
  local i=0 param=""

  # truncate the function name
  shift
  format="$(echo "$format" | sed 's/^[^ ]* //')"

  # check parameter numbers
  i=0
  for param in $format ; do
    if [ "$param" = "..." ]; then
      # ignore everything after '...'
      has_wild_arg=1
      break
    fi
    i=$(($i + 1))
  done
  if [ $has_wild_arg = 1 ]; then
    [ $# -ge $i ] || err_die "$original_format: need $i+ params (got $#): $@"
  else
    [ $# -eq $i ] || err_die "$original_format: need $i params (got $#): $@"
  fi

  # check if we have any empty parameters
  i=1
  for param in "$@"; do
    local param_name=$(echo "$format" | cut -d" " -f $i)
    # forr params started with 'opt_', allow it to be empty.
    if [ -z "$param" -a "${param_name##opt_*}" != "" ]; then
      shift # remove the invoke command name
      err_die "check_param: $original_format: " \
              "param '$param_name' is empty: '$@'"
    fi
    i=$(($i + 1))
  done
}

# Executes a command, and provide messages if it failed.
silent_invoke() {
  local ret=$FLAGS_TRUE
  if [ "${FLAGS_dry_run}" = "${FLAGS_TRUE}" ]; then
    return $ret
  fi
  ( eval "$@" ) >_exec.stdout 2>_exec.stderr || ret=$?
  if [ "$ret" != "0" ]; then
    alert " Execution failed ($ret): $*"
    alert " Messages:"
    cat _exec.stdout >&2
    cat _exec.stderr >&2
  fi
  return $ret
}

# Prints a verbose message before calling silent_invoke.
invoke() {
  verbose_msg " * invoke: $@"
  silent_invoke "$@" || err_die "Execution FAILED."
}

alert_write_protection() {
  if ! cros_is_hardware_write_protected;  then
    echo "
    WARNING: Hardware write protection may be still enabled, and a RO update is
             scheduled. The update may fail.
    "
  fi
}

# Prints a standard "unofficial HWID" warning message
alert_unofficial_hwid() {
  # TODO(hungte) maybe we don't need this anymore - simply check if keys are
  # compatible is enough.
  echo "
  Your system is using an unofficial version of $1 firmware,
  and is forbidden to invoke this update.

  Please restore your original firmware with correct HWID,
  or invoke this update in factory mode.
  "
}

# Prints a standard "unkonwn platform" error message
alert_unknown_platform() {
  # Sytnax: alert_unknown_platform CURRENT EXPECTED
  echo "
  Sorry, this firmware update is only for $2 platform.
  Your system ($1) is either incompatible or using an
  unknown version of firmware.
  "
}

# Prints a "incompatible" warning message
alert_incompatible_firmware() {
  # Syntax: alert_incompatible_firmware reboot_will_autoupdate
  echo "
  Your current RO firmware is NOT compatible with the new RW firmware, and needs
  a RO update.  Please make sure you've DISABLED write protection for such
  special update."

  if [ "$1" = "${FLAGS_TRUE}" ]; then
    echo "
  A RO autoupdate is also scheduled in next boot.
  "
  fi

  echo "
  If you want to start a manual update immediately, please use command:

  sudo chromeos-firmwareupdate --mode=incompatible_update
  "
}

