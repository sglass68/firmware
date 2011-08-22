#!/bin/sh
#
# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# For factory and auto update, after shell-ball self-extracts, this script is
# called to update BIOS and EC firmware as per how many files are extracted.
# To simply design, THIS SCRIPT MUST BE EXECUTED IN A R/W EXCLUSIVE TEMP FOLDER.
# AND ALL FILENAMES FOR INPUT AND OUTPUT MUST NOT CONTAIN SPACE.
#
# Allowed commands are:
# - standard shell commands
# - flashrom
# - gbb_utility
# - crossystem
# All other special commands should be defined by a function in crosutil.sh.
#
# Temporary files should be named as "_*" to prevent confliction

# Updater for firmware v3 (two-stop)
# This is designed for ARM platform, with following assumption:
# 1. No EC firmware
# 2. No need to update RO in startup (no ACPI/ASL code dependency)
# 3. Perform updates even if active firmware is "developer mode" (with
#    root-shell)

SCRIPT_BASE="$(dirname "$0")"
. "$SCRIPT_BASE/common.sh"

# Use bundled tools with highest priority, to prevent dependency when updating
PATH=".:$PATH"; export PATH

# ----------------------------------------------------------------------------
# Customization Section

# Customization script file name - do not change this.
# You have to create a file with this name to put your customization.
CUSTOMIZATION_SCRIPT="updater_custom.sh"

# Customization script main entry - do not change this.
# You have to define a function with this name to run your customization.
CUSTOMIZATION_MAIN="updater_custom_main"

# Customization script "RW compatible check" function.
# Overrides this with any function names to test if RW firmware in current
# system is compatible with this updater. The function must returns $FLAGS_FALSE
# if RW is not compatible (i.e., need incompatible_update mode)
CUSTOMIZATION_RW_COMPATIBLE_CHECK=""

# ----------------------------------------------------------------------------
# Constants

# Slot names defined by ChromeOS Firmware Specification
SLOT_A="RW_SECTION_A"
SLOT_B="RW_SECTION_B"
SLOT_RO="RO_SECTION"
SLOT_RW_SHARED="RW_SHARED"

FWSRC_NORMAL="$SLOT_B"
FWSRC_DEVELOPER="$SLOT_A"

TYPE_MAIN="main"
IMAGE_MAIN="bios.bin"

# ----------------------------------------------------------------------------
# Global Variables

# Current system identifiers (may be empty if running on non-ChromeOS systems)
HWID="$(crossystem hwid 2>/dev/null)" || HWID=""

# Compare following values with TARGET_FWID, TARGET_PLATFORM
# (should be passed by wrapper as environment variables)
FWID="$(crossystem fwid 2>/dev/null)" || FWID=""
PLATFORM="$(mosys platform name 2>/dev/null)" || PLATFORM=""

# RO update flags are usually enabled only in customization.
FLAGS_update_ro_main="$FLAGS_FALSE"

# TARGET_UNSTABLE is non-zero if the firmware is not stable.
: ${TARGET_UNSTABLE:=}

# ----------------------------------------------------------------------------
# Parameters

DEFINE_string mode "" \
 "Updater mode ( startup | bootok | autoupdate | todev | tonormal |"\
" recovery | factory_install | factory_final | incompatible_update )" "m"
DEFINE_boolean debug $FLAGS_FALSE "Enable debug messages." "d"
DEFINE_boolean verbose $FLAGS_TRUE "Enable verbose messages." "v"
DEFINE_boolean dry_run $FLAGS_FALSE "Enable dry-run mode." ""
DEFINE_boolean force $FLAGS_FALSE "Try to force update." ""

DEFINE_boolean check_keys $FLAGS_TRUE "Check firmware keys before updating." ""
DEFINE_boolean check_wp $FLAGS_TRUE \
  "Check if write protection is enabled before updating RO sections" ""
DEFINE_boolean check_rw_compatible $FLAGS_TRUE \
  "Check if RW firmware is compatible with current RO" ""
DEFINE_boolean check_platform $FLAGS_TRUE \
  "Bypass firmware updates if the system platform name is different" ""
# Required for factory compatibility
DEFINE_boolean factory $FLAGS_FALSE "Equivalent to --mode=factory_install"

# ----------------------------------------------------------------------------
# General Updater

# Update Main Firmware (BIOS, SPI)
update_mainfw() {
  # Syntax: update_mainfw SLOT FIRMWARE_SOURCE_TYPE
  #    Write assigned type (normal/developer) of firmware source into
  #    assigned slot (returns directly if the target is already filled
  #    with correct data)
  # Syntax: update_mainfw SLOT
  #    Write assigned slot from MAIN_TARGET_IMAGE.
  # Syntax: update_mainfw
  #    Write complete MAIN_TARGET_IMAGE

  local slot="$1"
  local source_type="$2"
  debug_msg "invoking: update_mainfw($@)"
  # TODO(hungte) verify if slot is valid.
  [ -s "$IMAGE_MAIN" ] || err_die "missing firmware image: $IMAGE_MAIN"
  if [ "$slot" = "" ]; then
    invoke "flashrom $TARGET_OPT_MAIN -w $IMAGE_MAIN"
  elif [ "$source_type" = "" ]; then
    invoke "flashrom $TARGET_OPT_MAIN -i $slot -w $IMAGE_MAIN"
  else
    local section_file="$DIR_TARGET/$TYPE_MAIN/$source_type"
    [ -s "$section_file" ] || err_die "update_mainfw: missing $section_file"
    invoke "flashrom $TARGET_OPT_MAIN -i $slot:$section_file -w $IMAGE_MAIN"
  fi
}

dup2_mainfw() {
  # Syntax: dup2_mainfw SLOT_FROM SLOT_TO
  #    Duplicates a slot to another place on system live firmware
  local slot_from="$1" slot_to="$2"
  # Writing in flashrom, even if there's nothing to write, is still much slower
  # than reading because it needs to read + verify whole image. So we should
  # only write firmware on if there's something changed.
  local temp_from="_dup2_temp_from"
  local temp_to="_dup2_temp_to"
  local temp_image="_dup2_temp_image"
  debug_msg "invoking: dup2_mainfw($@)"
  local opt_read_slots="-i $slot_from:$temp_from -i $slot_to:$temp_to"
  invoke "flashrom $TARGET_OPT_MAIN $opt_read_slots -r $temp_image"
  if [ -s "$temp_from" ] && cros_compare_file "$temp_from" "$temp_to"; then
    debug_msg "dup2_mainfw: slot content is the same, no need to copy."
  else
    invoke "flashrom $TARGET_OPT_MAIN -i $slot_to:$temp_from -w $temp_image"
  fi
}

# ----------------------------------------------------------------------------
# Helper functions

# Note this will change $IMAGE_MAIN so any processing to the file (ex,
# prepare_main_image) must be invoked AFTER this call.
preserve_vpd() {
  crosfw_dupe_vpd "-i RO_VPD -i RW_VPD" "$IMAGE_MAIN" ""
}

preserve_hwid() {
  [ -s "$IMAGE_MAIN" ] || err_die "preserve_hwid: no main firmware."
  silent_invoke "gbb_utility -s --hwid='$HWID' $IMAGE_MAIN"
}

preserve_bmpfv() {
  if [ -z "$HWID" ]; then
    debug_msg "preserve_bmpfv: Running on non-ChromeOS firmware system. Skip."
    return
  fi
  debug_msg "Preseving main firmware images..."
  [ -s "$IMAGE_MAIN" ] || err_die "preserve_bmpfv: no main firmware."
  silent_invoke "flashrom $TARGET_OPT_MAIN -i GBB:_gbb.bin -r _temp.rom"
  silent_invoke "gbb_utility -g --bmpfv=_bmpfv.bin _gbb.bin"
  [ -s "_bmpfv.bin" ] || err_die "preserve_bmpfv: invalid bmpfv"
  silent_invoke "gbb_utility -s --bmpfv=_bmpfv.bin $IMAGE_MAIN"
}

# Compares two slots from current and target folder.
is_equal_slot() {
  check_param "is_equal_slot(type, slot, opt_slot2)" "$@"
  local type_name="$1" slot_name="$2" slot2_name="$3"
  [ -n "$slot2_name" ] || slot2_name="$slot_name"
  local current="$DIR_CURRENT/$type_name/$slot_name"
  local target="$DIR_TARGET/$type_name/$slot2_name"
  cros_compare_file "$current" "$target"
}

# Verifies if current system is installed with compatible rootkeys
check_compatible_keys() {
  local current_image="$DIR_CURRENT/$IMAGE_MAIN"
  local target_image="$DIR_TARGET/$IMAGE_MAIN"
  if [ "${FLAGS_check_keys}" = "${FLAGS_FALSE}" ]; then
    debug_msg "check_compatible_keys: ignored."
    return $FLAGS_TRUE
  fi
  if ! cros_check_same_root_keys "$current_image" "$target_image"; then
    alert_incompatible_rootkey
    err_die "Incompatible Rootkey."
  fi
  # unpack image for checking TPM
  local rootkey="_rootkey"
  silent_invoke "gbb_utility -g --rootkey=$rootkey $target_image" 2>/dev/null ||
    true
  if ! cros_check_tpm_key_version "$DIR_TARGET/$TYPE_MAIN/VBLOCK_A" \
                                  "$DIR_TARGET/$TYPE_MAIN/FW_MAIN_A" \
                                  "$rootkey"; then
    alert_incompatible_tpmkey
    err_die "Incompatible TPM Key."
  fi
}

prepare_main_image() {
  crosfw_unpack_image "$TYPE_MAIN" "$IMAGE_MAIN" "$TARGET_OPT_MAIN"
}

prepare_main_current_image() {
  crosfw_unpack_current_image "$TYPE_MAIN" "$IMAGE_MAIN" "$TARGET_OPT_MAIN" "$@"
}

is_mainfw_write_protected() {
  if [ "$FLAGS_check_wp" = $FLAGS_FALSE ]; then
    verbose_msg "Warning: write protection checking is bypassed."
    return $FLAGS_FALSE
  fi
  if ! cros_is_hardware_write_protected; then
    false
  else
    flashrom $TARGET_OPT_MAIN --wp-status 2>/dev/null |
      grep -q "write protect is enabled"
  fi
}

clear_update_cookies() {
  # Always success because the system may not have crossystem ready yet if we're
  # trying to recover a broken firmware or after transition from legacy firmware
  ( cros_set_fwb_tries 0
    cros_set_startup_update_tries 0 ) >/dev/null 2>&1 ||
      debug_msg "clear_update_cookies: there were some errors, ignored."
}

# ----------------------------------------------------------------------------
# Core logic in different modes

# Startup
mode_startup() {
  debug_msg "Updating at startup is deprecated for current system."
  # We may still have trouble updating some systems' RO, if their S3 resume runs
  # through the RO path (ex, most x86).  That's only a problem during
  # development, since in production the RO section is, well, read-only. If
  # we're willing to force a reboot after updating RO, we're ok.
  ( cros_set_startup_update_tries 0 ) >/dev/null 2>&1 || true
}

# Update Engine - Current Boot Successful (chromeos_setgoodkernel)
mode_bootok() {
  # TODO(hungte) Quick check by startup_update_tries or VBLOCK preamble

  local mainfw_act="$(cros_get_prop mainfw_act)"
  # Copy firmware to the spare slot.
  # flashrom will check if we really need to update the bits
  if [ "$mainfw_act" = "A" ]; then
    dup2_mainfw "$SLOT_A" "$SLOT_B"
  elif [ "$mainfw_act" = "B" ]; then
    dup2_mainfw "$SLOT_B" "$SLOT_A"
  else
    err_die "bootok: unexpected active firmware ($mainfw_act)..."
  fi
  cros_set_fwb_tries 0
}

# Update Engine - Received Update
mode_autoupdate() {
  # Quick check if we need to update
  local need_update=0
  if [ "$TARGET_FWID" != "$FWID" ]; then
    need_update=1
  fi

  # TODO(hungte) Remove the need_update=1 which forces updating when VBLOCK key
  # version by crossystem is ready. Quick check is reliable only if VBLOCK key
  # and FWID both match.
  need_update=1

  if [ "$need_update" -eq 0 ] && [ "${FLAGS_force}" != "${FLAGS_TRUE}" ]; then
    verbose_msg "Your system is already installed with latest firmware."
    return
  fi

  local mainfw_act="$(cros_get_prop mainfw_act)"
  if [ "$mainfw_act" = "B" ]; then
    err_die_need_reboot "Done (retry update next boot)"
  elif [ "$mainfw_act" != "A" ]; then
    err_die "autoupdate: unexpected active firmware ($mainfw_act)..."
  fi

  prepare_main_image
  prepare_main_current_image

  # Check whole RW_SECTION to decide if we need to update.
  if is_equal_slot "$TYPE_MAIN" "$SLOT_A" "$FWSRC_NORMAL"; then
    verbose_msg "RW Section is the same, no need to update."
    return
  fi

  check_compatible_keys
  update_mainfw "$SLOT_B" "$FWSRC_NORMAL"
  cros_set_fwb_tries 6
}

# Transition to Developer Mode
mode_todev() {
  cros_set_prop dev_boot_usb=1
  echo "
  Booting from USB device is enabled.  Insert bootable media into USB / SDCard
  slot and press Ctrl-U in developer screen to boot your own image.
  "
  clear_update_cookies
}

# Transition to Normal Mode
mode_tonormal() {
  # This is optional because whenever you turn off developer switch, the
  # dev_boot_usb is also cleared by firmware.
  cros_set_prop dev_boot_usb=0
  echo "Booting from USB device is disabled."
  clear_update_cookies
}

# Recovery Installer
mode_recovery() {
  if ! is_mainfw_write_protected; then
    verbose_msg "mode_recovery: update RO+RW"
    preserve_vpd
    preserve_bmpfv
    update_mainfw
  else
    # TODO(hungte) check if FMAP is not changed
    verbose_msg "mode_recovery: update main/RW:A,B,SHARED"
    prepare_main_image
    prepare_main_current_image
    check_compatible_keys
    update_mainfw "$SLOT_A" "$FWSRC_NORMAL"
    update_mainfw "$SLOT_B" "$FWSRC_NORMAL"
    update_mainfw "$SLOT_RW_SHARED"
  fi
  clear_update_cookies
}

# Factory Installer
mode_factory_install() {
  # Everything executed here must assume the system may be not using ChromeOS
  # firmware.
  if is_mainfw_write_protected; then
    # TODO(hungte) check if we really need to stop user by comparing firmware
    # image, bit-by-bit.
    err_die "You need to first disable hardware write protection switch."
  fi
  preserve_vpd || verbose_msg "Warning: cannot preserve VPD."
  # We may preserve bitmap here, just like recovery mode. However if there's
  # some issue (or incompatible stuff) found in bitmap, we will need a method to
  # update the bitmaps.
  update_mainfw
  clear_update_cookies || true
}

# Factory Wipe
mode_factory_final() {
  verbose_msg "Factory finalization is complete."
  # For two-stop firmware, we have nothing to do in finalization stage.
  clear_update_cookies
}

# Updates for incompatible RW firmware (need to update RO)
mode_incompatible_update() {
  if is_mainfw_write_protected; then
    # TODO(hungte) check if we really need to stop user by comparing
    # RO firmware image, bit-by-bit.
    err_die "You need to first disable hardware write protection switch."
  fi
  mode_recovery
}

# ----------------------------------------------------------------------------
# Main Entry

main_check_rw_compatible() {
  local is_compatible="${FLAGS_TRUE}"
  if [ "${FLAGS_check_rw_compatible}" = "${FLAGS_FALSE}" ]; then
    verbose_msg "Bypassed RW compatbility check. You're on your own."
    return $is_compatible
  fi

  if [ -n "${TARGET_UNSTABLE}" ]; then
    debug_msg "Current image is tagged as UNSTABLE."
    if [ "$FWID" != "$TARGET_FWID" ]; then
      verbose_msg "Found unstable firmware $TARGET_FWID. Current: $FWID."
      is_compatible="${FLAGS_FALSE}"
    fi
  fi

  # Try explicit match
  if [ -n "$CUSTOMIZATION_RW_COMPATIBLE_CHECK" ]; then
    debug_msg "Checking customized RW compatibility..."
    "$CUSTOMIZATION_RW_COMPATIBLE_CHECK" || is_compatible="${FLAGS_ERROR}"
  fi

  case "$is_compatible" in
    "${FLAGS_FALSE}" )
      verbose_msg "Try to update with recovery mode..."
      mode_recovery
      ;;
    "${FLAGS_ERROR}" )
      verbose_msg "
        RW firmware update is not compatible with current RO firmware.
        Starting full update...
        "
      mode_incompatible_update
      ;;
    * )
      true
  esac
  return $is_compatible
}

LOCK_FILE="/tmp/chromeos-firmwareupdate-running"

drop_lock() {
  rm -f "$LOCK_FILE"
}

acquire_lock() {
  if [ -r "$LOCK_FILE" ]; then
    err_die "Firmware Updater already running ($LOCK_FILE). Please retry later."
  fi
  touch "$LOCK_FILE"
  # Clean up on regular or error exits.
  trap drop_lock EXIT
}

main() {
  acquire_lock

  # factory compatibility
  if [ "${FLAGS_factory}" = "${FLAGS_TRUE}" ] ||
     [ "${FLAGS_mode}" = "factory" ]; then
    FLAGS_mode=factory_install
  fi

  verbose_msg "Starting $TARGET_PLATFORM firmware updater v3 (${FLAGS_mode})..."
  verbose_msg " - Updater package: [$TARGET_FWID]"
  verbose_msg " - Current system:  [$FWID]"
  # quick check and setup for basic envoronments
  if [ -n "$HWID" ]; then
    # always preserve HWID for current system, if available.
    preserve_hwid
    debug_msg "preserved HWID as: $HWID."
  fi

  # Check platform except in factory_install mode.
  if [ "${FLAGS_check_platform}" = "${FLAGS_TRUE}" ] &&
     [ "${FLAGS_mode}" != "factory_install" ] &&
     [ -n "$TARGET_PLATFORM" ] &&
     [ "$PLATFORM" != "$TARGET_PLATFORM" ]; then
    alert_unknown_platform "$PLATFORM" "$TARGET_PLATFORM"
    exit 1
  fi

  # load customization
  if [ -r "$CUSTOMIZATION_SCRIPT" ]; then
    debug_msg "loading customization..."
    . ./$CUSTOMIZATION_SCRIPT
    # invoke customization
    debug_msg "starting customized updater main..."
    $CUSTOMIZATION_MAIN
  fi

  case "${FLAGS_mode}" in
    # Modes which update RW firmware only and only in normal mode; these need to
    # verify if existing RO firmware is compatible.  If not, try to perform
    # RO+RW update.
    autoupdate )
      debug_msg "mode with dev and compatibility check: ${FLAGS_mode}"
        main_check_rw_compatible &&
        mode_"${FLAGS_mode}"
      ;;

    # Modes which update RW firmware only; these need to verify if existing RO
    # firmware is compatible.  If not, try to perform RO+RW update.
    recovery )
      debug_msg "mode with compatibility check: ${FLAGS_mode}"
      main_check_rw_compatible &&
        mode_"${FLAGS_mode}"
      ;;

    # Modes which don't mix existing RO firmware with new RW firmware from the
    # updater.  They either copy RW firmware between EEPROM slots, or copy both
    # RO+RW from the shellball.  Either way, RO+RW compatibility is assured.
    startup | bootok | todev | tonormal | factory_install | factory_final | \
      incompatible_update )
      debug_msg "mode without incompatible checks: ${FLAGS_mode}"
      mode_"${FLAGS_mode}"
      ;;

    "" )
      err_die "Please assign updater mode by --mode option."
      ;;

    * )
      err_die "Unknown mode: ${FLAGS_mode}"
      ;;
  esac
  verbose_msg "Firmware update (${FLAGS_mode}) completed."
}

# Parse command line
FLAGS "$@" || exit 1
eval set -- "$FLAGS_ARGV"

# Exit on error
set -e

# Main Entry
main
