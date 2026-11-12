from django.db import models
from db_connection import db, administrators, mirrors, content, sites

# MongoDB Collections
administrators_collection = administrators
mirror_collection = mirrors
content_collection = content
sites_collection = sites
