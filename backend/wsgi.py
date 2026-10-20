"""Entry point:  python wsgi.py   (development)   |   gunicorn -w 1 --threads 8 wsgi:app   (production)."""
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app import create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("FLASK_HOST", "0.0.0.0"), port=int(os.environ.get("FLASK_PORT", "5000")))
