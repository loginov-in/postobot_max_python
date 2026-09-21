import ssl

from maxapi.connection.base import BaseConnection

from config import MAX_BOT_TOKEN

PLATFORM_API_URL = "https://platform-api2.max.ru"

_SSL_ALLOW = ssl.create_default_context()
_SSL_ALLOW.check_hostname = False
_SSL_ALLOW.verify_mode = ssl.CERT_NONE

_original_request = BaseConnection.request


async def _patched_request(self, method, path, model=None, is_return_raw=False, **kwargs):
    params = kwargs.get("params")
    if isinstance(params, dict):
        kwargs["params"] = {k: v for k, v in params.items() if k != "access_token"}

    headers = dict(kwargs.get("headers") or {})
    headers["Authorization"] = MAX_BOT_TOKEN
    kwargs["headers"] = headers

    kwargs["ssl"] = _SSL_ALLOW

    return await _original_request(self, method, path, model, is_return_raw, **kwargs)


def patch_transport() -> None:
    BaseConnection.API_URL = PLATFORM_API_URL
    BaseConnection.request = _patched_request