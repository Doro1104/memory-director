#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v xcodebuild >/dev/null || { echo 'Install Xcode and select its Command Line Tools first.'; exit 1; }
mkdir -p work/ios-build
xcodebuild -project ios/MemoryDirector.xcodeproj -scheme MemoryDirector -configuration Debug \
  -destination 'generic/platform=iOS Simulator' -derivedDataPath work/ios-build \
  CODE_SIGNING_ALLOWED=NO build
ditto -c -k --keepParent work/ios-build/Build/Products/Debug-iphonesimulator/MemoryDirector.app \
  work/ios-build/MemoryDirector-simulator.zip
echo 'Built simulator-only ZIP. Physical iPhone installation still requires user signing in Xcode.'
