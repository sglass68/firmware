// Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// TODO(hungte) Current implementation is to invoke external commands by shell
// execution. We should move to native library calls directly in future.

#include "system_utility.h"

#include <string>

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/reboot.h>
#include <unistd.h>

using std::string;

// Command names of system property utility "crossystem".
const string kSysPropertyCommand = "crossystem";
const char *kStartupTries = "fwupdate_tries";
const char *kCurrentWriteProtectSwitch = "wpsw_cur";
const char *kBootTimeWriteProtectSwitch = "wpsw_boot";
const char *kNewFirmwareTries = "fwb_tries";
const char *kVerifiedBootDataFlags = "vdat_flags";
const char *kTpmFirmwareKeyVersion = "tpm_fwver";
const char *kTpmKernelKeyVersion = "tpm_kernver";

// Utility functions.
string IntToString(int i) {
  char buffer[64];

  snprintf(buffer, sizeof(buffer), "%d", i);
  return buffer;
}

int StringToInt(const string &s, int default_value=0) {
  int v;
  if (sscanf(s.c_str(), "%i", &v) == 1)
    return v;
  return default_value;
}

// ChromeOS implmentation for SystemUtility.
bool SystemUtility::ShellOutput(const string &command, string *output) {
  char buffer[256];  // A small buffer for simple commands.
  FILE *pipe = popen(command.c_str(), "r");

  // TODO(hungte) Cache stderr output to temporary file, and log on error.
  while (fgets(buffer, sizeof(buffer), pipe)) {
    if (output)
      *output += buffer;
  }

  return pclose(pipe) == 0;
}

bool SystemUtility::GetProperty(const string &key, string *result,
                                bool die_on_failure) {
  // TODO(hungte) Every ChromeOS images should have "crossystem" command, but
  // when we do this in auto update mode it may execute the one from old OS
  // image.  To fix that, we should replace this by statically linking
  // crossystem directly.
  bool succeed = ShellOutput(kSysPropertyCommand + " " + key, result);
  if (!succeed && die_on_failure)
    Die("Failed to get system property: %s\b", key.c_str());
  return succeed;
}

bool SystemUtility::SetProperty(const string &key, const string &value) {
  return ShellOutput(kSysPropertyCommand + " " + key + "=" + value);
}

bool SystemUtility::SetStartupUpdateTries(int tries) {
  return SetProperty(kStartupTries, IntToString(tries));
}

int SystemUtility::GetStartupUpdateTries() {
  string result;
  GetProperty(kStartupTries, &result);
  return StringToInt(result);
}

bool SystemUtility::SetNewFirmwareTries(int tries) {
  return SetProperty(kNewFirmwareTries, IntToString(tries));
}

int SystemUtility::GetNewFirmwareTries() {
  string result;
  GetProperty(kNewFirmwareTries, &result);
  return StringToInt(result);
}

bool SystemUtility::IsOneStopMode() {
  // See VBSD_LF_USE_RO_NORMAL (0x08) in vboot_reference.
  const int kOneStopFlag = 0x08;
  string result;

  GetProperty(kVerifiedBootDataFlags, &result);
  return (StringToInt(result) & kOneStopFlag);
}

bool SystemUtility::IsHardwareWriteProtected() {
  // Not every system can report "current write protection switch status", so we
  // must fallback to boot time record when current value is not available.
  string result;

  if (!GetProperty(kCurrentWriteProtectSwitch, &result, false) &&
      !GetProperty(kBootTimeWriteProtectSwitch, &result, false))
    Die("Failed to determine hardware write protection status");

  return StringToInt(result) == 1;
}

bool SystemUtility::IsSoftwareWriteProtected(const string &target) {
  const char *kEnabledStatus = "WP: write protect is enabled.";
  const char *kDisabledStatus = "WP: write protect is disabled.";
  string result;

  if (!ShellOutput("flashrom --wp-status -p internal:bus=" + target, &result))
    return false;

  if (result.find(kEnabledStatus) != string::npos)
    return true;
  if (result.find(kDisabledStatus) != string::npos)
    return false;

  Die("Unknown write protection status: %s", result.c_str());
  return false;
}

bool SystemUtility::Reboot() {
  // According to sync(2), Linux kernel should wait until actual writing is
  // done; however modern disks may have internal cache, so let's wait for few
  // seconds again.
  sync();
  sleep(3);

  // According to reboot(2), reboot should never return on success; however on
  // systems modified for fast reboot like ChromeOS, we did see some platforms
  // return immediately, so again we should wait for few seconds.
  reboot(RB_AUTOBOOT);
  sleep(60);
  Die("Failed to reboot");
  return false;
}

bool SystemUtility::ClearNonVolatileData() {
  // TODO(hungte) Replace this by native library call once we have libmosys.
  return ShellOutput("mosys nvram clear 2>&1");
}

int SystemUtility::GetTpmFirmwareVersion() {
  string result;
  GetProperty(kTpmFirmwareKeyVersion, &result);
  return StringToInt(result);
}

int SystemUtility::GetTpmKernelVersion() {
  string result;
  GetProperty(kTpmKernelKeyVersion, &result);
  return StringToInt(result);
}

void SystemUtility::Debug(const char *format, ...) {
  va_list ap;
  // TODO(hungte) Return early if not in debug mode.
  va_start(ap, format);
  fputs("[DEBUG] ", stderr);
  vfprintf(stderr, format, ap);
  fputs("\n", stderr);
  va_end(ap);
}

void SystemUtility::Alert(const char *format, ...) {
  va_list ap;
  va_start(ap, format);
  vfprintf(stderr, format, ap);
  fputs("\n", stderr);
  va_end(ap);
}

void SystemUtility::Die(const char *format, ...) {
  va_list ap;
  va_start(ap, format);
  fputs("[ERROR] ", stderr);
  vfprintf(stderr, format, ap);
  fputs("\n", stderr);
  va_end(ap);
  exit(1);
}
