import asyncio
import contextlib
from typing import Any

import httpx


class MetadataUnavailableError(RuntimeError):
    pass


async def metadata_request(client: httpx.AsyncClient, method: str, url: str, *, graphql: bool = False, **kwargs: Any) -> httpx.Response:
    for attempt in range(3):
        try:
            response = await getattr(client, method)(url, **kwargs)
            response.raise_for_status()
            if graphql:
                try:
                    payload = response.json()
                except ValueError as error:
                    raise MetadataUnavailableError("IMDb returned an invalid response") from error
                if not isinstance(payload, dict) or payload.get("errors") or not payload.get("data") or not isinstance(payload["data"], dict):
                    raise MetadataUnavailableError("IMDb returned an incomplete response")
            return response
        except (httpx.RequestError, httpx.HTTPStatusError, MetadataUnavailableError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in (408, 429) and error.response.status_code < 500:
                raise
            if attempt == 2:
                raise
            delay = 0.25 * 2**attempt
            if isinstance(error, httpx.HTTPStatusError):
                with contextlib.suppress(ValueError):
                    delay = min(5.0, max(delay, float(error.response.headers.get("Retry-After", "0"))))
            await asyncio.sleep(delay)
    raise MetadataUnavailableError("Metadata request failed")
