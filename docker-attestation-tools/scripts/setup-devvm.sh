#!/bin/bash

set -x

REPO="github.com/cjen1-msft/ccf"
BRANCH="main"

usage() {
	cat <<EOF
Usage: $0 [options]

Options:
	-r, --repo <repo>      Repository path (default: ${REPO})
	-b, --branch <branch>  Branch to checkout (default: ${BRANCH})
	-h, --help             Show this help and exit
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		-r|--repo)
			if [[ -z "$2" ]]; then echo "Missing value for $1" >&2; usage; exit 1; fi
			REPO="$2"; shift 2 ;;
		-b|--branch)
			if [[ -z "$2" ]]; then echo "Missing value for $1" >&2; usage; exit 1; fi
			BRANCH="$2"; shift 2 ;;
		-h|--help)
			usage; exit 0 ;;
		*)
			echo "Unknown argument: $1" >&2
			usage
			exit 1 ;;
	esac
done

tdnf install -y vim tmux git ca-certificates

git clone https://$REPO /ccf
cd /ccf

git remote add upstream https://github.com/microsoft/ccf
git remote set-url origin https://github.com/cjen1-msft/ccf
git fetch --all
git checkout $BRANCH

./scripts/setup-ci.sh

mkdir build
cd build
cmake -GNinja -DCOMPILE_TARGET=snp -DCMAKE_BUILD_TYPE=Debug -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DVERBOSE_LOGGING=ON -DSAN=ON -DUSE_SNMALLOC=OFF -DCLANG_TIDY=OFF ..
ninja

./tests.sh -VV -R nonexistent || true