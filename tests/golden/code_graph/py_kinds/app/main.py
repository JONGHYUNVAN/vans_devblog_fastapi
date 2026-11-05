from typing import TYPE_CHECKING

from app.core import VALUE

if TYPE_CHECKING:
    from app.typed import Typed


def run(arg: 'Typed') -> int:
    return VALUE
