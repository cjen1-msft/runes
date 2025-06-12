#!/bin/bash

set -x
BRANCH="local-sealing"

tdnf install -y vim tmux git ca-certificates

git clone https://github.com/microsoft/ccf /ccf
cd /ccf

git remote add upstream https://github.com/microsoft/ccf
git remote set-url origin https://github.com/cjen1-msft/ccf
git fetch --all
git checkout $BRANCH

./scripts/setup-ci.sh

mkdir build
cd build
cmake -GNinja -DCOMPILE_TARGET=snp -DCMAKE_BUILD_TYPE=Debug -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DVERBOSE_LOGGING=ON -DSAN=ON ..
ninja

./tests.sh -VV -R nonexistent || true
source env/bin/activate