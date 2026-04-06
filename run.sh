#!/bin/bash
# Quick start script for RAG Flask app

set -e  # Exit on error

echo "🚀 Starting RAG Assistant..."
echo ""

# Check if Ollama is running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  WARNING: Ollama is not running on http://localhost:11434"
    echo "   Start it with: ollama serve"
    echo ""
fi

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
    echo ""
fi

# Activate venv
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "📥 Installing dependencies..."
pip install -q -r requirements.txt

# Create data directories if they don't exist
mkdir -p data/raw/uploads
mkdir -p data/chroma

echo ""
echo "✅ Everything ready!"
echo ""
echo "🌐 Starting Flask server on http://localhost:5000"
echo "   Press Ctrl+C to stop"
echo ""

# Start the app
python app.py
