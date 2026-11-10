"""The product's own read API, under `/api`.

Distinct from `/sim`, which is the simulated marketplace feeding data in. This
package serves what the dashboard reads: runs, exceptions, investigations and
the human decisions on them.

Money crosses as `{"amount": "<string>", "currency": "XXX"}` and nothing here
does arithmetic on an amount (`specs/tech-stack.md` section 2).
"""

from reckonai.api.router import router

__all__ = ["router"]
