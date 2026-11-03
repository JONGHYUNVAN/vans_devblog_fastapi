from __future__ import annotations

from app.models.base import Base


class Item(Base):
    def to_service(self):
        from app.services.items import ItemService

        return ItemService
