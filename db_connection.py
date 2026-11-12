import os
import pymongo
from pymongo import MongoClient

# MongoDB Atlas connection configuration
MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    "mongodb+srv://amalpullan4_db_user:oaoZdFeUppQljcZA@cluster0.xg3zwpq.mongodb.net/"
)
DATABASE_NAME = os.environ.get("MONGODB_DB", "mirrorlink")

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
sites = db['sites']
mirrors = db['mirrors']
content = db['content']
