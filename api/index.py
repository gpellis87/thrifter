"""Vercel serverless entrypoint — re-exports the FastAPI app."""
import sys
from pathlib import Path

# Add project root to path so backend imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.main import app
