"""Application entry point.

Usage:
    flask --app run run         # dev server
    gunicorn run:app            # production (via Dockerfile)
"""

from app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)