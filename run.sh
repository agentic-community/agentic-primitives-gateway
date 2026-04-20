#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/configs"

CONFIG_NAME="${1:-quickstart}"
CONFIG_FILE="$CONFIG_DIR/$CONFIG_NAME.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config '$CONFIG_NAME' not found."
    echo ""
    echo "Available configs:"
    for f in "$CONFIG_DIR"/*.yaml; do
        name="$(basename "$f" .yaml)"
        desc="$(head -1 "$f" | sed 's/^## //')"
        printf "  %-20s %s\n" "$name" "$desc"
    done
    echo ""
    echo "Usage: ./run.sh <config-name>"
    exit 1
fi

echo "Starting server with config: $CONFIG_NAME"
echo "Config file: $CONFIG_FILE"
echo ""

export AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE="$CONFIG_FILE"
exec uvicorn agentic_primitives_gateway.main:app --reload --reload-dir src --reload-dir configs --reload-dir ui --host 0.0.0.0 --port "${PORT:-8000}"
