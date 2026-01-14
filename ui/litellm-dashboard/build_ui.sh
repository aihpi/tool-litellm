#!/bin/bash

if command -v node &> /dev/null; then
  NODE_MAJOR=$(node -v | tr -d "v" | cut -d "." -f 1)
  if [ "$NODE_MAJOR" -lt 20 ]; then
    if command -v nvm &> /dev/null; then
      nvm install v20
      nvm use v20
    else
      echo "Error: Node.js v20+ is required. Deployment aborted."
      exit 1
    fi
  fi
else
  if command -v nvm &> /dev/null; then
    nvm install v20
    nvm use v20
  else
    echo "Error: Node.js is not installed. Deployment aborted."
    exit 1
  fi
fi

# print contents of ui_colors.json
echo "Contents of ui_colors.json:"
cat ui_colors.json

# Run npm build
npm run build

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

  rm -rf ./out

  echo "Deployment completed."
else
  echo "Build failed. Deployment aborted."
fi
