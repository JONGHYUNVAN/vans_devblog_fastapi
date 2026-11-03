import os

from ..models.item import Item


class ItemService:
    def list_items(self):
        return [Item()], os.sep
