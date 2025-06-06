#!/usr/bin/env bash
set -e

ENV_DIR=".venv"

if [ ! -d "$ENV_DIR" ]; then
    echo "Creating uv virtual environment..."
    uv venv "$ENV_DIR"
    echo "Installing dependencies from requirements.txt..."
    uv pip install -r requirements.txt
else
    echo "Using existing virtual environment at $ENV_DIR"
fi

# shellcheck disable=SC1090
source "$ENV_DIR/bin/activate"
echo "Environment activated"
