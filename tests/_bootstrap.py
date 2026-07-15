"""Local-only import fallbacks for the standard-library test runner.

GitHub CI installs ``requirements-dev.txt`` and therefore never uses these
objects.  The fallbacks let contributors execute the isolated unit tests in a
restricted checkout where third-party packages are unavailable; any accidental
network request still fails immediately.
"""

from __future__ import annotations

import sys
import types
from typing import Any


def install_optional_dependency_stubs() -> None:
    try:
        import requests  # noqa: F401
    except ModuleNotFoundError:
        module = types.ModuleType("requests")

        class RequestException(RuntimeError):
            pass

        class HTTPError(RequestException):
            pass

        class ConnectionError(RequestException):
            pass

        class TooManyRedirects(RequestException):
            pass

        class Response:
            status_code = 200
            text = ""
            content = b""
            headers: dict[str, str] = {}
            url = ""
            request: Any = None

            def json(self) -> dict[str, Any]:
                return {}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise HTTPError(f"HTTP {self.status_code}")

        class Request:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.args = args
                self.kwargs = kwargs

            def prepare(self) -> "Request":
                return self

        def unavailable(*args: Any, **kwargs: Any) -> Response:
            raise ConnectionError("network access is disabled in the isolated test")

        class Session:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

            request = staticmethod(unavailable)
            get = staticmethod(unavailable)
            post = staticmethod(unavailable)

        module.RequestException = RequestException
        module.HTTPError = HTTPError
        module.ConnectionError = ConnectionError
        module.TooManyRedirects = TooManyRedirects
        module.Response = Response
        module.Request = Request
        module.Session = Session
        module.request = unavailable
        module.get = unavailable
        module.post = unavailable
        module.put = unavailable
        sys.modules["requests"] = module

    try:
        import bs4  # noqa: F401
    except ModuleNotFoundError:
        module = types.ModuleType("bs4")

        class BeautifulSoup:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("HTML parsing is outside the isolated unit test")

        module.BeautifulSoup = BeautifulSoup
        sys.modules["bs4"] = module
