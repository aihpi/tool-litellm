#!/bin/bash

# Check if UI_BASE_PATH argument is provided
if [ -z "$1" ]; then
    echo "Error: UI_BASE_PATH argument is required."
    echo "Usage: $0 <UI_BASE_PATH>"
    exit 1
fi

# Set UI_BASE_PATH from the first argument
UI_BASE_PATH="$1"

if command -v node &> /dev/null; then
    NODE_MAJOR=$(node -v | tr -d "v" | cut -d "." -f 1)
    if [ "$NODE_MAJOR" -lt 20 ]; then
        echo "Error: Node.js v20+ is required. Deployment aborted."
        exit 1
    fi
else
    echo "Error: Node.js is not installed. Deployment aborted."
    exit 1
fi

# Run npm build with the environment variable
UI_BASE_PATH=$UI_BASE_PATH npm run build

# Check if the build was successful
if [ $? -eq 0 ]; then
    echo "Build successful. Copying files..."

    # echo current dir
    echo
    pwd

    # Specify the destination directory
    destination_dir="../../litellm/proxy/_experimental/out"

    # Remove existing files in the destination directory
    rm -rf "$destination_dir"/*

    # Copy the contents of the output directory to the specified destination
    cp -r ./out/* "$destination_dir"

    echo "Deployment completed."
else
    echo "Build failed. Deployment aborted."
fi
