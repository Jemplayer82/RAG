#!/bin/bash
# Restart the RAG Flask app

echo "Stopping any existing Flask process..."
pkill -f "python app.py" 2>/dev/null || true

echo "Starting RAG Flask app..."
cd "$(dirname "$0")"
python app.py &

echo "Flask started. Open http://localhost:5000"
