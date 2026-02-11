import os
from app.db import Database

DB_PATH = os.getenv("DB_PATH", "data/data.db")
db = Database(DB_PATH)

