#!/usr/bin/env bash
set -e
python -m pip install -q -r requirements.txt
echo "AMLTrace starting on http://127.0.0.1:5000"
python app.py
