from pymongo import MongoClient
from functools import lru_cache
import os


@lru_cache()
def get_client():
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI is not set in .env")
    return MongoClient(uri)


def get_db():
    return get_client()[os.getenv("MONGO_DB", "pulse-dev")]
