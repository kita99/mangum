import logging
from contextlib import ExitStack
from typing import (
    Any,
    ContextManager,
    Callable,
    Dict,
    Optional,
    TYPE_CHECKING,
)

from .exceptions import ConfigurationError
from .handlers import AbstractHandler
from .protocols import HTTPCycle, WebSocketCycle, LifespanCycle
from .backends import WebSocket
from .types import ASGIApp, WsRequest


if TYPE_CHECKING:  # pragma: no cover
    from awslambdaric.lambda_context import LambdaContext


DEFAULT_TEXT_MIME_TYPES = [
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/vnd.api+json",
]


logger = logging.getLogger("mangum")


class Mangum:
    """
    Creates an adapter instance.

    * **app** - An asynchronous callable that conforms to version 3.0 of the ASGI
    specification. This will usually be an ASGI framework application instance.
    * **lifespan** - A string to configure lifespan support. Choices are `auto`, `on`,
    and `off`. Default is `auto`.
    * **api_gateway_base_path** - Base path to strip from URL when using a custom
    domain name.
    * **text_mime_types** - A list of MIME types to include with the defaults that
    should not return a binary response in API Gateway.
    * **dsn** - A connection string required to configure a supported WebSocket backend.
    * **api_gateway_endpoint_url** - A string endpoint url to use for API Gateway when
    sending data to WebSocket connections. Default is to determine this automatically.
    * **api_gateway_region_name** - A string region name to use for API Gateway when
    sending data to WebSocket connections. Default is `AWS_REGION` environment variable.
    """

    app: ASGIApp
    lifespan: str = "auto"
    dsn: Optional[str] = None
    api_gateway_endpoint_url: Optional[str] = None
    api_gateway_region_name: Optional[str] = None
    connect_hook: Optional[Callable] = None
    disconnect_hook: Optional[Callable] = None

    def __init__(
        self,
        app: ASGIApp,
        lifespan: str = "auto",
        dsn: Optional[str] = None,
        api_gateway_endpoint_url: Optional[str] = None,
        api_gateway_region_name: Optional[str] = None,
        connect_hook: Optional[Callable] = None,
        disconnect_hook: Optional[Callable] = None,
        **handler_kwargs: Dict[str, Any]
    ) -> None:
        self.app = app
        self.lifespan = lifespan
        self.dsn = dsn
        self.api_gateway_endpoint_url = api_gateway_endpoint_url
        self.api_gateway_region_name = api_gateway_region_name
        self.handler_kwargs = handler_kwargs
        self.connect_hook = connect_hook
        self.disconnect_hook = disconnect_hook

        if self.lifespan not in ("auto", "on", "off"):
            raise ConfigurationError(
                "Invalid argument supplied for `lifespan`. Choices are: auto|on|off"
            )

        if connect_hook and not callable(connect_hook):
            raise Exception("Invalid connect_hook supplied. Must be a callable")

        if disconnect_hook and not callable(disconnect_hook):
            raise Exception("Invalid disconnect_hook supplied. Must be callable")

    def __call__(self, event: dict, context: "LambdaContext") -> dict:
        logger.debug("Event received.")

        with ExitStack() as stack:
            if self.lifespan != "off":
                lifespan_cycle: ContextManager = LifespanCycle(self.app, self.lifespan)
                stack.enter_context(lifespan_cycle)

            handler = AbstractHandler.from_trigger(
                event, context, **self.handler_kwargs
            )
            request = handler.request

            if isinstance(request, WsRequest):
                api_gateway_endpoint_url = (
                    self.api_gateway_endpoint_url or handler.api_gateway_endpoint_url
                )
                websocket = WebSocket(
                    dsn=self.dsn,
                    api_gateway_endpoint_url=api_gateway_endpoint_url,
                    api_gateway_region_name=self.api_gateway_region_name,
                    connect_hook=self.connect_hook,
                    disconnect_hook=self.disconnect_hook,
                )
                websocket_cycle = WebSocketCycle(
                    request, handler.message_type, handler.connection_id, websocket
                )
                response = websocket_cycle(self.app, handler.body)
            else:
                http_cycle = HTTPCycle(request)
                response = http_cycle(self.app, handler.body)

        return handler.transform_response(response)
