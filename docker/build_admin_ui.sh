#!/bin/bash

set -euo pipefail

# print current dir 
echo
pwd


echo "Building Admin UI..."

# Install curl only if missing. Retrying package-manager installs makes the
# Docker build more tolerant of transient mirror/network errors.
if ! command -v curl &> /dev/null; then
    # Check if we are on macOS
    if [[ "$(uname)" == "Darwin" ]]; then
        # Install dependencies using Homebrew
        if ! command -v brew &> /dev/null; then
            echo "Error: Homebrew not found. Please install Homebrew and try again."
            exit 1
        fi
        brew update
        brew install curl
    else
        # Assume Linux, try using apt-get
        if command -v apt-get &> /dev/null; then
            for i in 1 2 3; do
                apt-get update && apt-get install -y curl && break || sleep 5
            done
        elif command -v apk &> /dev/null; then
            # Try using apk if apt-get is not available
            for i in 1 2 3; do
                apk update && apk add --no-cache curl && break || sleep 5
            done
        else
            echo "Error: Unsupported package manager. Cannot install dependencies."
            exit 1
        fi

        if ! command -v curl &> /dev/null; then
            echo "Error: curl installation failed after retries."
            exit 1
        fi
    fi
fi
NVM_VERSION="v0.40.4"
NVM_CHECKSUM="4b7412c49960c7d31e8df72da90c1fb5b8cccb419ac99537b737028d497aba4f"
NVM_SCRIPT=$(mktemp)
trap 'rm -f "$NVM_SCRIPT"' EXIT
curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" -o "$NVM_SCRIPT"
if command -v sha256sum &>/dev/null; then
  echo "${NVM_CHECKSUM}  ${NVM_SCRIPT}" | sha256sum -c -
elif command -v shasum &>/dev/null; then
  echo "${NVM_CHECKSUM}  ${NVM_SCRIPT}" | shasum -a 256 -c -
else
  echo "No sha256 tool found; cannot verify nvm checksum"; exit 1
fi || { echo "nvm checksum verification failed"; exit 1; }
bash "$NVM_SCRIPT"
source ~/.nvm/nvm.sh
NODE_VERSION="$(cat ui/litellm-dashboard/.nvmrc)"
nvm install "v${NODE_VERSION}"
nvm use "v${NODE_VERSION}"

# cd in to /ui/litellm-dashboard
cd ui/litellm-dashboard

# ensure have access to build_ui.sh
chmod +x ./build_ui.sh

# install dependencies
npm install --no-audit --no-fund

# run ./build_ui.sh
./build_ui.sh

# return to root directory
cd ../..
