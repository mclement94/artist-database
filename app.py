# app.py
"""
Entry point for running the Flask app locally.

On Render / Raspberry Pi you can also point your process manager at:
    artistdb:create_app()

but for local dev this is the simplest.
"""

from artistdb import create_app

app = create_app()

if __name__ == "__main__":
    # Local development server
    app.run(host="127.0.0.1", port=5000, debug=True)
