from django.db import models
from db_connection import db
# Create your models here.


administrators_collection = db['administrators']
mirror_collection = db['mirrors']
content_collection = db['content']
sites_collection = db['sites']