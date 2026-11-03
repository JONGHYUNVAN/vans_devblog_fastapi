from app.dependencies import get_item_service
from app.models.item import Item
from app.services import items as item_services


def list_items():
    return get_item_service(), Item, item_services
