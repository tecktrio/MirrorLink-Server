from django.db import models
from db_connection import db, administrators, users, plans, mirrors, content, sites

# MongoDB Collections
administrators_collection = administrators
users_collection = users
plans_collection = plans
mirror_collection = mirrors
content_collection = content
sites_collection = sites
