#!/bin/zsh
# Builds the two tools as tiny app bundles so macOS attributes Bluetooth access to them.
set -e
cd "$(dirname "$0")"
for t in scan:GarminBLEScan:garmin-ble-scan sync:GarminBLESync:garmin-ble-sync; do
  src=${t%%:*}; rest=${t#*:}; app=${rest%%:*}; bin=${rest#*:}
  mkdir -p $app.app/Contents/MacOS
  swiftc -O $src.swift -o $app.app/Contents/MacOS/$bin
  sed "s#<key>CFBundleName</key>#<key>CFBundleExecutable</key><string>$bin</string><key>CFBundlePackageType</key><string>APPL</string><key>LSUIElement</key><true/><key>CFBundleName</key>#; s#garmin-ble-scan#$bin#g" Info.plist > $app.app/Contents/Info.plist
  codesign -s - --force $app.app
done
