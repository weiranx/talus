#!/bin/zsh
# usage: ./run.sh scan [secs] | ./run.sh sync [--name "fenix 9"] [--download 2] ...
cd "$(dirname "$0")"
tool=$1; shift
app=$([[ $tool == scan ]] && echo GarminBLEScan || echo GarminBLESync)
out="$PWD/$tool-$(date +%Y%m%d-%H%M%S).log"
open -W -n --stdout "$out" --stderr "$out" $app.app --args "$@"
cat "$out"
