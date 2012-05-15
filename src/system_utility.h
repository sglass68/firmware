// Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CROSFW_SYSTEM_UTILITY_H_
#define CROSFW_SYSTEM_UTILITY_H_

#include <string>

class SystemUtility {
 public:

  // Executes shell command and returns output and result.
  bool ShellOutput(const std::string &command, std::string *output=NULL);

  // Gets a system property, using names defined in "crossystem".
  bool GetProperty(const std::string &key, std::string *result,
                   bool die_on_failure=true);

  // Sets a system property by one key value pair.
  bool SetProperty(const std::string &key, const std::string &value);

  // Sets system startup time counter for tries of firmware update.
  bool SetStartupUpdateTries(int tries);

  // Gets system startup time counter for tries of firmware update.
  int GetStartupUpdateTries();

  // Sets system counter for trying new firmware (usually in slot B).
  bool SetNewFirmwareTries(int tries);

  // Gets system counter for trying new firmware (usually in slot B).
  int GetNewFirmwareTries();

  // Gets the stored firmware key version in TPM (to prevent rollback).
  int GetTpmFirmwareVersion();

  // Gets the stored kernel key version in TPM (to prevent rollback).
  int GetTpmKernelVersion();

  // Returns system hardware write protection status.
  bool IsHardwareWriteProtected();

  // Returns software write protection status on target.
  bool IsSoftwareWriteProtected(const std::string &target);

  // Returns if current system was booted in one-stop mode (also known as
  // "RO-Normal") firmware.
  bool IsOneStopMode();

  // Reboots system.
  bool Reboot();

  // Clears Non-Volatile (CMOS or MBR) system data.
  bool ClearNonVolatileData();

  // Prints message in debug mode.
  void Debug(const char *format, ...);

  // Prints message to console.
  void Alert(const char *format, ...);

  // Prints error message and aborts immediatly.
  void Die(const char *format, ...);
};

#endif  // CROSFW_SYSTEM_UTILITY_H_
