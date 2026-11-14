import os
from pathlib import Path
from dotenv import load_dotenv
import pymongo
from pymongo import MongoClient

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(os.path.join(BASE_DIR, ".env"))

MONGODB_URI = os.environ.get("MONGODB_URI")
DATABASE_NAME = os.environ.get("MONGODB_DB", "mirrorlink")

if not MONGODB_URI:
    raise ValueError("[MongoDB] MONGODB_URI is not set in environment or .env file.")

print(f"[MongoDB] Initializing database connection to '{DATABASE_NAME}'...")

try:
    client = MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=20000,
        retryWrites=True
    )
    # Ping the admin database to verify connectivity
    client.admin.command('ping')
    db = client[DATABASE_NAME]
    print(f"[MongoDB] Successfully connected to MongoDB Atlas database: {db.name}")
except Exception as e:
    print(f"[MongoDB] CRITICAL: Failed to connect to MongoDB Atlas: {e}")
    raise e

# Application collection handles
administrators = db['administrators']
users = db['users']
plans = db['plans']
sites = db['sites']
mirrors = db['mirrors']
content = db['content']
