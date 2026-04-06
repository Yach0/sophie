from typing import TYPE_CHECKING

from beanie import Link as BeanieLink

if TYPE_CHECKING:  # pylint: disable=undefined-variable
    type Link[T] = T
else:
    Link = BeanieLink
