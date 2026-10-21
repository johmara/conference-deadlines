#!/bin/sh
# Copy the built design system into the repo so GitHub Pages can serve it as static files.
# Run after `npm install` / bumping @johmara/design-system, then commit vendor/ and assets/.
set -e
pkg=node_modules/@johmara/design-system
mkdir -p vendor assets
cp "$pkg/dist/design-system.css" vendor/design-system.css
cp "$pkg/assets/monogram.png" assets/monogram.png
