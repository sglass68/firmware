#!/bin/sh
#
# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
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

# Updater for firmware v4 (two-stop main, chromeos-ec).
# This is designed for x86/arm platform, using chromeos-ec (software sync).
# Assume SLOT_A and SLOT_B has exactly same contents (and same keyblock).

SCRIPT_BASE="$(dirname "$0")"
. "$SCRIPT_BASE/common.sh"

# Use bundled tools with highest priority, to prevent dependency when updating
cros_setup_path

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
SLOT_LEGACY="RW_LEGACY"
SLOT_EC_RO="EC_RO"
SLOT_EC_RW="EC_RW"
SLOT_PD_RO="EC_RO"
SLOT_PD_RW="EC_RW"

# Main = Application (AP) or SoC firmware, sometimes considered as "BIOS"
TYPE_MAIN="main"
# EC = Embedded Controller
TYPE_EC="ec"
# PD = Power Delivery
TYPE_PD="pd"

# ----------------------------------------------------------------------------
# Global Variables

# Current system identifiers (may be empty if running on non-ChromeOS systems)
HWID="$(crossystem hwid 2>/dev/null)" || HWID=""
ECINFO="$(mosys -k ec info 2>/dev/null)" || ECINFO=""
PDINFO="$(mosys -k pd info 2>/dev/null)" || PDINFO=""

# Compare following values with TARGET_*
# (should be passed by wrapper as environment variables)
FWID="$(crossystem fwid 2>/dev/null)" || FWID=""
RO_FWID="$(crossystem ro_fwid 2>/dev/null)" || RO_FWID=""
ECID="$(eval "$ECINFO"; echo "$fw_version")"
PDID="$(eval "$PDINFO"; echo "$fw_version")"
PLATFORM="${FWID%%.*}"

# This should be overriden via a custom_updater.sh script.
LEGACY_UPGRADE_WHITELIST=""

# ----------------------------------------------------------------------------
# Helper functions

# Verifies if current system is installed with compatible rootkeys
check_compatible_keys() {
  local current_image="$DIR_CURRENT/$IMAGE_MAIN"
  local target_image="$DIR_TARGET/$IMAGE_MAIN"
  if [ "${FLAGS_check_keys}" = ${FLAGS_FALSE} ]; then
    debug_msg "check_compatible_keys: ignored."
    return $FLAGS_TRUE
  fi
  if ! cros_check_same_root_keys "$current_image" "$target_image"; then
    alert_incompatible_rootkey
    die "Incompatible Rootkey."
  fi

  # Get RW firmware information
  local fw_info
  fw_info="$(cros_get_rw_firmware_info "$DIR_TARGET/$TYPE_MAIN/VBLOCK_A" \
                                       "$DIR_TARGET/$TYPE_MAIN/FW_MAIN_A" \
                                       "$target_image")" || fw_info=""
  [ -n "$fw_info" ] || die "Failed to get RW firmware information"

  # Check TPM
  if ! cros_check_tpm_key_version "$fw_info"; then
    alert_incompatible_tpmkey
    die "Incompatible TPM Key."
  fi

  # Warn for RO-normal updates
  local flag_ro_normal_boot=1
  local current_flags="$(cros_get_firmware_preamble_flags "$fw_info")"
  if [ "$((current_flags & flag_ro_normal_boot))" = "$flag_ro_normal_boot" ]
  then
    alert "
    WARNING: FIRMWARE IMAGE TO BE UPDATED IS SIGNED WITH 'RO-NORMAL' FLAG.
    THIS IS A KEY-BLOCK-ONLY UPDATE WITHOUT FIRMWARE CODE CHANGE.
    YOUR FWID (ACTIVE FIRMWARE ID) WON'T CHANGE AFTER APPLYING THIS UPDATE.
    "
  fi
}

need_update_legacy() {
  local legacy_hash="$(crosfw_slot_hash "$TYPE_MAIN" "$SLOT_LEGACY")"
  debug_msg "RW_LEGACY hash $legacy_hash, white list: $LEGACY_UPGRADE_WHITELIST"

  for hash in $LEGACY_UPGRADE_WHITELIST; do
    if [ "$legacy_hash" = "$hash" ]; then
      debug_msg "RW_LEGACY needs update."
      return $FLAGS_TRUE
    fi
  done

  debug_msg "No updates for RW_LEGACY"
  return $FLAGS_FALSE
}

need_update_main() {
  if [ "$TARGET_FWID" != "$FWID" ]; then
    return $FLAGS_TRUE
  fi

  prepare_main_image
  prepare_main_current_image

  if is_vboot2; then
    # Slot A and B are not guaranteed to be the same with vboot2, and we do not
    # always boot out of slot A.  This check must first determine what the
    # active slot is and compare that instead of always comparing A to B.
    local mainfw_act="$(cros_get_prop mainfw_act)"
    local vblock=""

    if [ "$mainfw_act" = "A" ]; then
      vblock="VBLOCK_A"
    elif [ "$mainfw_act" = "B" ]; then
      vblock="VBLOCK_B"
    else
      die "autoupdate: unexpected active firmware ($_mainfw_act)..."
    fi

    # Compare VBLOCK from current and target slot (normal firmware).
    if ! crosfw_is_equal_slot "$TYPE_MAIN" "$vblock" "$vblock"; then
      debug_msg "$vblock differs between current and target"
      return $FLAGS_TRUE
    fi
  else
    # Compare VBLOCK from current A slot and target B slot (normal firmware).
    if ! crosfw_is_equal_slot "$TYPE_MAIN" "VBLOCK_A" "VBLOCK_B"; then
      debug_msg "VBLOCK in A and B differ"
      return $FLAGS_TRUE
    fi
  fi

  # Compare legacy hash with whitelisted set of legacy hashes to update.
  need_update_legacy
}

need_update_ec() {
  prepare_ec_image
  prepare_ec_current_image
  if ! is_ecfw_write_protected &&
     ! crosfw_is_equal_slot "$TYPE_EC" "$SLOT_EC_RO"; then
      debug_msg "EC RO needs update."
      return $FLAGS_TRUE
  fi
  if ! crosfw_is_equal_slot "$TYPE_EC" "$SLOT_EC_RW"; then
      debug_msg "EC RW needs update."
      return $FLAGS_TRUE
  fi
  return $FLAGS_FALSE
}

need_update_pd() {
  prepare_pd_image
  prepare_pd_current_image
  if ! is_pdfw_write_protected &&
     ! crosfw_is_equal_slot "$TYPE_PD" "$SLOT_PD_RO"; then
      debug_msg "PD RO needs update."
      return $FLAGS_TRUE
  fi
  if ! crosfw_is_equal_slot "$TYPE_PD" "$SLOT_PD_RW"; then
      debug_msg "PD RW needs update."
      return $FLAGS_TRUE
  fi
  return $FLAGS_FALSE
}

prepare_main_image() {
  crosfw_unpack_image "$TYPE_MAIN" "$IMAGE_MAIN" "$TARGET_OPT_MAIN"
}

prepare_ec_image() {
  crosfw_unpack_image "$TYPE_EC" "$IMAGE_EC" "$TARGET_OPT_EC"
}

prepare_pd_image() {
  crosfw_unpack_image "$TYPE_PD" "$IMAGE_PD" "$TARGET_OPT_PD"
}

prepare_main_current_image() {
  crosfw_unpack_current_image "$TYPE_MAIN" "$IMAGE_MAIN" "$TARGET_OPT_MAIN" "$@"
}

prepare_ec_current_image() {
  crosfw_unpack_current_image "$TYPE_EC" "$IMAGE_EC" "$TARGET_OPT_EC" "$@"
}

prepare_pd_current_image() {
  crosfw_unpack_current_image "$TYPE_PD" "$IMAGE_PD" "$TARGET_OPT_PD" "$@"
}

is_write_protection_disabled() {
  if [ "${FLAGS_update_main}" = ${FLAGS_TRUE} ]; then
    is_mainfw_write_protected && return $FLAGS_FALSE || true
  fi

  if [ "${FLAGS_update_ec}" = ${FLAGS_TRUE} ]; then
    is_ecfw_write_protected && return $FLAGS_FALSE || true
  fi

  if [ "${FLAGS_update_pd}" = ${FLAGS_TRUE} ]; then
    is_pdfw_write_protected && return $FLAGS_FALSE || true
  fi

  return $FLAGS_TRUE
}

clear_update_cookies() {
  # Always success because the system may not have crossystem ready yet if we're
  # trying to recover a broken firmware or after transition from legacy firmware
  ( cros_set_fwb_tries 0
    cros_set_startup_update_tries 0
    cros_set_prop recovery_request=0 ) >/dev/null 2>&1 ||
      debug_msg "clear_update_cookies: there were some errors, ignored."
}

silent_sh() {
  # Calls given commands and ignores any error (mostly for factory
  # installations, when the firmware is still non-Chrome).
  ( "$@" ) >/dev/null 2>&1 ||
    debug_msg "Failed calling: $@"
}

enable_dev_boot() {
  cros_set_prop dev_boot_usb=1 dev_boot_signed_only=0
}

disable_dev_boot() {
  # The firmware will decide and reset default values of dev_boot_usb and
  # dev_boot_signed_only on reoot when user turned off developer switch (i.e.,
  # normal mode). It's safe to set dev_boot_usb to zero here, but
  # dev_boot_signed_only may expect different default values, so we leave it
  # untouched and let firmware decide.
  cros_set_prop dev_boot_usb=0
}

load_keyset() {
  if ! [ -d "$KEYSET_DIR" ]; then
    debug_msg "No keysets folder."
    return
  fi
  local keyid="$FLAGS_customization_id"
  if [ -z "$keyid" ]; then
    debug_msg "No customization ID from command line. Try to read from VPD."
    keyid="$(vpd -g "customization_id" || echo "")"
  fi
  if [ -z "$keyid" ]; then
    verbose_msg "No customization_id in VPD/command line. Using default keys."
    return
  fi
  # "customization_id" in format LOEMID[-SERIES]. We only want LOEMID for now.
  keyid="${keyid%%-*}"
  debug_msg "Keysets detected, using [$keyid]"
  local rootkey="$KEYSET_DIR/rootkey.$keyid"
  local vblock_a="$KEYSET_DIR/vblock_A.$keyid"
  local vblock_b="$KEYSET_DIR/vblock_B.$keyid"
  if ! [ -s "$rootkey" ]; then
    die "Failed to load keysets for customization ID [$keyid]."
  fi
  # Override keys
  gbb_utility -s --rootkey="$rootkey" "$IMAGE_MAIN" ||
    die "Failed to update rootkey from keyset [$keyid]."
  local size_input="$(cros_get_file_size "$IMAGE_MAIN")"
  local param="dummy:emulate=VARIABLE_SIZE,image=$IMAGE_MAIN,size=$size_input"
  local write_list="-i VBLOCK_A:$vblock_a -i VBLOCK_B:$vblock_b"
  silent_invoke "flashrom -p $param -w $IMAGE_MAIN $write_list" ||
    die "Failed to update VBLOCK from keyset [$keyid]."
  # TODO(hungte) Verify key correctness after building new image files.
  verbose_msg "Firmware keys changed to set [$keyid]."
}

is_vboot2() {
  crossystem 'fw_vboot2?1'
}

# ----------------------------------------------------------------------------
# Core logic in different modes

# Startup
mode_startup() {
  # ChromeOS-EC do not need to be updated at startup time.
  cros_set_startup_update_tries 0
}

# Update Engine - Current Boot Successful (chromeos_setgoodkernel)
mode_bootok() {
  if is_vboot2; then
    cros_set_prop fw_result=success fw_try_count=0
  else
    local mainfw_act="$(cros_get_prop mainfw_act)"
    # Copy main firmware to the spare slot.
    if [ "$mainfw_act" = "A" ]; then
      crosfw_dup2_mainfw "$SLOT_A" "$SLOT_B"
    elif [ "$mainfw_act" = "B" ]; then
      crosfw_dup2_mainfw "$SLOT_B" "$SLOT_A"
    else
      # Recovery mode, or non-chrome.
      die "bootok: abnormal active firmware ($mainfw_act)..."
    fi
    cros_set_fwb_tries 0
  fi

  # EC firmware is managed by software sync (main firmware).
}

# Update Engine - Received Update
mode_autoupdate() {
  # Quick check if we need to update
  local need_update=0
  if [ "${FLAGS_force}" = ${FLAGS_TRUE} ]; then
    need_update=1
  else
    # Check main firmware
    if [ "${FLAGS_update_main}" = ${FLAGS_TRUE} ]; then
      if need_update_main; then
        need_update=1
      else
        FLAGS_update_main=$FLAGS_FALSE
      fi
    fi
    # Check EC firmware
    if [ "${FLAGS_update_ec}" = ${FLAGS_TRUE} ]; then
      if [ "$TARGET_ECID" != "$ECID" ]; then
        need_update=1
      else
        FLAGS_update_ec=$FLAGS_FALSE
      fi
    fi
    # Check PD firmware
    if [ "${FLAGS_update_pd}" = ${FLAGS_TRUE} ]; then
      if [ "$TARGET_PDID" != "$PDID" ]; then
        need_update=1
      else
        FLAGS_update_pd=$FLAGS_FALSE
      fi
    fi
  fi

  if [ "$need_update" -eq 0 ]; then
    verbose_msg "Latest firmware already installed. No need to update."
    return
  fi

  if [ "${FLAGS_update_main}" = "${FLAGS_TRUE}" ]; then
    local mainfw_act="$(cros_get_prop mainfw_act)"
    local update_slot="${SLOT_B}"
    local prop_name=fwb_tries
    local try_next=

    if is_vboot2; then
      if [ "$mainfw_act" = "A" ]; then
        update_slot="$SLOT_B"
        try_next=B
      elif [ "$mainfw_act" = "B" ]; then
        update_slot="$SLOT_A"
        try_next=A
      else
        die "autoupdate: unexpected active firmware ($_mainfw_act)..."
      fi
      prop_name=fw_try_count
    else  # vboot1
      if [ "$mainfw_act" = "B" ]; then
        # mainfw_act is only updated at system reboot; if two updates (both with
        # firmware updates) are pushed in a row, next update will be executed
        # while mainfw_act is still B. Since we don't use RW BIOS directly when
        # updater is running, it should be safe to update in this case.
        debug_msg "mainfw_act=B, checking if we can still update FW B..."
        prepare_main_current_image
        cros_compare_file "$DIR_CURRENT/$TYPE_MAIN/$SLOT_A" \
                          "$DIR_CURRENT/$TYPE_MAIN/$SLOT_B" &&
          alert "Installing updates while mainfw_act is B (should be safe)." ||
          die_need_reboot "Done (retry update next boot)"
      elif [ "$mainfw_act" != "A" ]; then
        die "autoupdate: unexpected active firmware ($mainfw_act)..."
      fi
    fi


    prepare_main_image
    prepare_main_current_image
    check_compatible_keys
    crosfw_update_main "$update_slot"
    if [ -n "$try_next" ]; then
      cros_set_prop "fw_try_next=$try_next"
    fi

    #Update legacy if needed
    if need_update_legacy; then
      crosfw_update_main "$SLOT_LEGACY"
    fi

    # Try to determine EC software sync by checking $ECID. We can't rely on
    # $TARGET_ECID, $FLAGS_update_ec or $IMAGE_EC because in future there may be
    # no EC binary blobs in updater (integrated inside main firmware image).
    # Note since $ECID uses mosys, devices not using ChromeOS EC may still
    # report an ID (but they should use updater v3 instead).
    if [ -n "$ECID" -a "$ECID" != "$TARGET_ECID" ]; then
      # EC software sync may need extra reboots.
      cros_set_prop "${prop_name}=8"
      verbose_msg "On reboot EC update may occur."
    else
      cros_set_prop "${prop_name}=6"
    fi
  fi

  # EC updates will be handled by EC software sync.
}

# Transition to Developer Mode
mode_todev() {
  enable_dev_boot
  echo "
  Booting any self-signed kernel from SSD/USB/SDCard slot is enabled.
  Insert bootable media into USB / SDCard slot and press Ctrl-U in developer
  screen to boot your own image.
  "
  clear_update_cookies
}

# Transition to Normal Mode
mode_tonormal() {
  # This is optional because whenever you turn off developer switch, the
  # dev_boot_usb is also cleared by firmware.
  disable_dev_boot
  echo "Booting from USB device is disabled."
  clear_update_cookies
}

# Recovery Installer
mode_recovery() {
  local prefix="mode_recovery"
  [ "${FLAGS_mode}" = "recovery" ] || prefix="${FLAGS_mode}(recovery)"
  if [ "${FLAGS_update_main}" = ${FLAGS_TRUE} ]; then
    if ! is_mainfw_write_protected; then
      verbose_msg "$prefix: update RO+RW"
      crosfw_preserve_vpd
      crosfw_preserve_gbb
      crosfw_preserve_nvram
      crosfw_update_main
    else
      # TODO(hungte) check if FMAP is not changed
      verbose_msg "$prefix: update main/RW:A,B,SHARED"
      prepare_main_image
      prepare_main_current_image
      check_compatible_keys
      crosfw_update_main "$SLOT_A"
      crosfw_update_main "$SLOT_B"
      crosfw_update_main "$SLOT_RW_SHARED"
    fi
  fi

  if [ "${FLAGS_update_ec}" = ${FLAGS_TRUE} ]; then
    if ! is_ecfw_write_protected; then
      verbose_msg "$prefix: update ec/RO+RW"
      crosfw_update_ec
    fi
    # If EC is write-protected, software sync will lock RW EC after kernel
    # starts (left firmware boot stage). We can't "recovery" EC RW in this case.
    # Ref: crosbug.com/p/16087.
    verbose_msg "$prefix: EC may be restored or updated in next boot."
  fi

  if [ "${FLAGS_update_pd}" = ${FLAGS_TRUE} ]; then
    if ! is_pdfw_write_protected; then
      verbose_msg "$prefix: update pd/RO+RW"
      crosfw_update_pd
    fi
    # If PD is write-protected, software sync will lock RW PD after kernel
    # starts (left firmware boot stage). We can't recover PD RW in this case.
    # Ref: crosbug.com/p/16087.
    verbose_msg "$prefix: PD may be restored or updated in next boot."
  fi

  clear_update_cookies
}

# Factory Installer
mode_factory_install() {
  # Everything executed here must assume the system may be not using ChromeOS
  # firmware.
  is_write_protection_disabled ||
    die_need_ro_update "You need to first disable hardware write protection."

  if [ "${FLAGS_update_main}" = ${FLAGS_TRUE} ]; then
    # We may preserve bitmap here, just like recovery mode. However if there's
    # some issue (or incompatible stuff) found in bitmap, we will need a method
    # to update the bitmaps.
    crosfw_preserve_vpd || verbose_msg "Warning: cannot preserve VPD."
    crosfw_update_main
  fi
  if [ "${FLAGS_update_ec}" = ${FLAGS_TRUE} ]; then
    crosfw_update_ec
  fi
  if [ "${FLAGS_update_pd}" = ${FLAGS_TRUE} ]; then
    crosfw_update_pd
  fi
  cros_clear_nvdata
  silent_sh enable_dev_boot
  clear_update_cookies
}

# Factory Wipe
mode_factory_final() {
  silent_sh disable_dev_boot
  clear_update_cookies
}

# Updates for incompatible RW firmware (need to update RO)
mode_incompatible_update() {
  # TODO(hungte) check if we really need to stop user by comparing RO firmware
  # image, bit-by-bit.
  is_write_protection_disabled ||
    die_need_ro_update "You need to first disable hardware write protection."
  mode_recovery
}

# Checks if current system firmware version number is different than the one
# bundled in updater. Note root/recovery keys, TPM version, vblock key versions,
# and firmware integrity are all unchecked.
mode_fast_version_check() {
  if [ -n "$IMAGE_MAIN" -a "$RO_FWID" != "$TARGET_RO_FWID" ]; then
    die "Main RO FWID: $RO_FWID != $TARGET_RO_FWID"
  fi
  if [ -n "$IMAGE_MAIN" -a "$FWID" != "$TARGET_FWID" ]; then
    die "Main FWID: $FWID != $TARGET_FWID"
  fi
  if [ -n "$IMAGE_EC" -a "$ECID" != "$TARGET_ECID" ]; then
    die "EC FWID: $ECID != $TARGET_ECID"
  fi
  if [ -n "$IMAGE_PD" -a "$PDID" != "$TARGET_PDID" ]; then
    die "PD FWID: $PDID != $TARGET_PDID"
  fi
}

# ----------------------------------------------------------------------------
# Main Entry

main_check_rw_compatible() {
  local is_compatible="${FLAGS_TRUE}"
  if [ "${FLAGS_check_rw_compatible}" = ${FLAGS_FALSE} ]; then
    verbose_msg "Bypassed RW compatbility check. You're on your own."
    return $is_compatible
  fi

  # Try explicit match
  if [ -n "$CUSTOMIZATION_RW_COMPATIBLE_CHECK" ]; then
    debug_msg "Checking customized RW compatibility..."
    "$CUSTOMIZATION_RW_COMPATIBLE_CHECK" || is_compatible="${FLAGS_ERROR}"
  elif [ "$is_compatible" = "${FLAGS_TRUE}" ]; then
    cros_check_stable_firmware || is_compatible="${FLAGS_FALSE}"
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

main() {
  cros_acquire_lock
  set_flags
  [ -n "${UNIBUILD}" ] && cros_set_unibuild_vars

  # factory compatibility
  if [ "${FLAGS_factory}" = ${FLAGS_TRUE} ] ||
     [ "${FLAGS_mode}" = "factory" ]; then
    FLAGS_mode=factory_install
  fi
  local ro_type=""
  cros_is_ro_normal_boot && ro_type="$ro_type[RO_NORMAL]"

  verbose_msg "Starting $TARGET_PLATFORM firmware updater v4 (${FLAGS_mode})..."
  local package_info="$TARGET_FWID"
  if [ "$TARGET_FWID" != "$TARGET_RO_FWID" ]; then
    package_info="RO:$TARGET_RO_FWID RW:$TARGET_FWID"
  fi
  local current_info="RO:$RO_FWID $ro_type, ACT:$FWID"
  if [ -n "${TARGET_ECID%IGNORE}" ]; then
    package_info="$package_info / EC:$TARGET_ECID"
  fi
  if [ -n "$ECID" ]; then
    current_info="$current_info / EC:$ECID"
  fi
  if [ -n "${TARGET_PDID%IGNORE}" ]; then
    package_info="$package_info / PD:$TARGET_PDID"
  fi
  if [ -n "$PDID" ]; then
    current_info="$current_info / PD:$PDID"
  fi
  verbose_msg " - Updater package: [$package_info]"
  verbose_msg " - Current system:  [$current_info]"

  if [ "${FLAGS_update_main}" = ${FLAGS_TRUE} -a -n "${HWID}" ]; then
    # always preserve HWID for current system, if available.
    crosfw_preserve_hwid
    debug_msg "preserved HWID as: $HWID."
  fi

  local wpmsg="$(cros_report_wp_status $FLAGS_update_main \
                 $FLAGS_update_ec $FLAGS_update_pd)"
  verbose_msg " - Write protection: $wpmsg"

  # Check platform except in factory_install mode.
  if [ "${FLAGS_check_platform}" = ${FLAGS_TRUE} ] &&
     [ "${FLAGS_mode}" != "factory_install" ] &&
     ! cros_check_compatible_platform "${TARGET_PLATFORM}" "${PLATFORM}"; then
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

  load_keyset

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
      incompatible_update | fast_version_check )
      debug_msg "mode without incompatible checks: ${FLAGS_mode}"
      mode_"${FLAGS_mode}"
      ;;

    "" )
      die "Please assign updater mode by --mode option."
      ;;

    * )
      die "Unknown mode: ${FLAGS_mode}"
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
