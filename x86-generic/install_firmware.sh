#!/bin/sh

# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.# Use of this
# source code is governed by a BSD-style license that can be
# found in the LICENSE file.

uudecode -o - $0 | tar zxf -

echo "Flash system firmware..."

echo "Flash EC firmware..."

exit 0

