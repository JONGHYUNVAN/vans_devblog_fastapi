from app import dependencies
from app.routers import items


def create_app():
    return items, dependencies
