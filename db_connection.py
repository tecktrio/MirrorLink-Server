import pymongo

url = "mongodb+srv://tecktrio:tecktrio@testing.lnciw.mongodb.net/"
client = pymongo.MongoClient(url)

db = client['mirrorlink']