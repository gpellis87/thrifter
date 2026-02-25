#!/usr/bin/env python3
"""Start the Thrifter app server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, reload=True)
