import os
import json
import pymongo
from bson import ObjectId

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_db.json")

def to_jsonable(obj):
    if isinstance(obj, ObjectId):
        return {"$oid": str(obj)}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    return obj

def from_jsonable(obj):
    if isinstance(obj, dict):
        if "$oid" in obj and len(obj) == 1:
            return ObjectId(obj["$oid"])
        return {k: from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_jsonable(v) for v in obj]
    return obj

url = os.environ.get("MONGODB_URI", "mongodb+srv://tecktrio:tecktrio@testing.lnciw.mongodb.net/")
use_fallback = False

try:
    client = pymongo.MongoClient(url, serverSelectionTimeoutMS=2000)
    client.admin.command('ping')
    db = client['mirrorlink']
    print("Connected to MongoDB successfully.")
except Exception as e:
    print(f"Notice: MongoDB connection failed ({e}). Falling back to persistent local_db.json database.")
    use_fallback = True

if use_fallback:
    import mongomock
    mock_client = mongomock.MongoClient()
    raw_db = mock_client['mirrorlink']

    def load_data():
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    data = json.load(f)
                    for col_name, docs in data.items():
                        col = raw_db[col_name]
                        col.delete_many({})
                        if docs:
                            converted_docs = from_jsonable(docs)
                            col.insert_many(converted_docs)
                return
            except Exception as e:
                print(f"Warning: Failed to load local_db.json: {e}")

        # Seed default administrator if file doesn't exist or is empty
        admin_doc = {
            "_id": ObjectId("672f00000000000000000001"),
            "username": "admin",
            "password": "password",
            "email_id": "admin@mirrorlink.com",
            "profile_image_url": "",
            "contact": "1234567890",
            "address_line_1": "Localhost",
            "address_line_2": "",
            "address_line_3": "",
            "login_key": "adminpassword"
        }
        raw_db["administrators"].insert_one(admin_doc)
        save_data()

    def save_data():
        try:
            data = {}
            for col_name in ["administrators", "sites", "mirrors", "content"]:
                docs = list(raw_db[col_name].find())
                data[col_name] = to_jsonable(docs)
            with open(DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save local_db.json: {e}")

    load_data()

    class PersistentCollection:
        def __init__(self, col, name):
            self._col = col
            self.name = name

        def insert_one(self, doc, *args, **kwargs):
            res = self._col.insert_one(doc, *args, **kwargs)
            save_data()
            return res

        def insert_many(self, docs, *args, **kwargs):
            res = self._col.insert_many(docs, *args, **kwargs)
            save_data()
            return res

        def update_one(self, *args, **kwargs):
            res = self._col.update_one(*args, **kwargs)
            save_data()
            return res

        def update_many(self, *args, **kwargs):
            res = self._col.update_many(*args, **kwargs)
            save_data()
            return res

        def delete_one(self, *args, **kwargs):
            res = self._col.delete_one(*args, **kwargs)
            save_data()
            return res

        def delete_many(self, *args, **kwargs):
            res = self._col.delete_many(*args, **kwargs)
            save_data()
            return res

        def __getattr__(self, name):
            return getattr(self._col, name)

    class PersistentDatabase:
        def __init__(self, db):
            self._db = db
            self._collections = {}

        def __getitem__(self, name):
            if name not in self._collections:
                self._collections[name] = PersistentCollection(self._db[name], name)
            return self._collections[name]

        def __getattr__(self, name):
            return getattr(self._db, name)

    db = PersistentDatabase(raw_db)
