#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# generate-image-variants.sh — Generate Multi-Format Image Variants for CDN
#
# Generates WebP and AVIF variants for all JPEG/PNG images in the source directory.
# Produces multiple size variants (320w, 640w, 1280w, 1920w) for responsive images.
#
# Requirements: imagemagick, cwebp, avifenc (libavif-tools)
# Install: sudo apt-get install imagemagick webp libavif-tools
#
# Usage: ./generate-image-variants.sh <source_dir> <output_dir>
#
# Chapter 14 — Mobile-First Architecture for iGaming

set -euo pipefail

SOURCE_DIR="${1:-./images/source}"
OUTPUT_DIR="${2:-./images/optimised}"
WIDTHS=(320 640 1280 1920)
JPEG_QUALITY=85
WEBP_QUALITY=80
AVIF_QUALITY=70

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

check_dependencies() {
    local missing=()
    for cmd in convert cwebp avifenc; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing[*]}"
        log_error "Install: sudo apt-get install imagemagick webp libavif-tools"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Image processing functions
# ---------------------------------------------------------------------------

generate_jpeg() {
    local input="$1"
    local output="$2"
    local width="$3"
    convert "$input" \
        -resize "${width}x>" \
        -quality "$JPEG_QUALITY" \
        -strip \
        -interlace Plane \
        "$output"
}

generate_webp() {
    local input="$1"
    local output="$2"
    local width="$3"
    local tmp_png
    tmp_png=$(mktemp /tmp/imgvariant_XXXXXX.png)

    convert "$input" -resize "${width}x>" "$tmp_png"
    cwebp -q "$WEBP_QUALITY" -metadata none "$tmp_png" -o "$output" 2>/dev/null
    rm -f "$tmp_png"
}

generate_avif() {
    local input="$1"
    local output="$2"
    local width="$3"
    local tmp_png
    tmp_png=$(mktemp /tmp/imgvariant_XXXXXX.png)

    convert "$input" -resize "${width}x>" "$tmp_png"
    avifenc --min 0 --max 63 -q "$AVIF_QUALITY" "$tmp_png" "$output" 2>/dev/null
    rm -f "$tmp_png"
}

# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

process_image() {
    local input_path="$1"
    local rel_path="${input_path#"$SOURCE_DIR/"}"
    local base_name="${rel_path%.*}"
    local output_base="${OUTPUT_DIR}/${base_name}"

    mkdir -p "$(dirname "$output_base")"

    for width in "${WIDTHS[@]}"; do
        log_info "Processing ${rel_path} @ ${width}w"

        # JPEG
        if generate_jpeg "$input_path" "${output_base}-${width}w.jpg" "$width"; then
            log_info "  JPEG: ${output_base}-${width}w.jpg"
        else
            log_warn "  JPEG failed for ${width}w"
        fi

        # WebP
        if generate_webp "$input_path" "${output_base}-${width}w.webp" "$width"; then
            log_info "  WebP: ${output_base}-${width}w.webp"
        else
            log_warn "  WebP failed for ${width}w"
        fi

        # AVIF
        if generate_avif "$input_path" "${output_base}-${width}w.avif" "$width"; then
            log_info "  AVIF: ${output_base}-${width}w.avif"
        else
            log_warn "  AVIF failed for ${width}w"
        fi
    done
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
    check_dependencies

    if [[ ! -d "$SOURCE_DIR" ]]; then
        log_error "Source directory not found: $SOURCE_DIR"
        exit 1
    fi

    mkdir -p "$OUTPUT_DIR"

    local count=0
    local errors=0

    while IFS= read -r -d '' input_file; do
        if process_image "$input_file"; then
            (( count++ )) || true
        else
            (( errors++ )) || true
        fi
    done < <(find "$SOURCE_DIR" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) -print0)

    log_info "Done: processed ${count} images, ${errors} errors"
    log_info "Output directory: ${OUTPUT_DIR}"

    # Generate srcset manifest for reference
    manifest_path="${OUTPUT_DIR}/image-manifest.json"
    log_info "Writing manifest to ${manifest_path}"
    python3 - <<EOF
import json, os, glob

manifest = {}
output_dir = "${OUTPUT_DIR}"
widths = [${WIDTHS[*]}]

for jpg in glob.glob(f"{output_dir}/**/*-${WIDTHS[-1]}w.jpg", recursive=True):
    base = jpg.replace(f"-${WIDTHS[-1]}w.jpg", "")
    rel = base.replace(output_dir + "/", "")
    manifest[rel] = {
        "jpeg": [f"{rel}-{w}w.jpg" for w in widths],
        "webp": [f"{rel}-{w}w.webp" for w in widths],
        "avif": [f"{rel}-{w}w.avif" for w in widths],
        "widths": widths,
    }

with open("${manifest_path}", "w") as f:
    json.dump(manifest, f, indent=2)
print(f"Manifest written: {len(manifest)} entries")
EOF

    [[ $errors -eq 0 ]] || exit 1
}

main "$@"
