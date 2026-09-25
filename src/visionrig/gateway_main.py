from __future__ import annotations

import uvicorn

from .gateway import GatewayConfig, create_gateway_app


def main() -> None:
    config = GatewayConfig.from_env()
    app = create_gateway_app(config)
    uvicorn.run(
        app,
        host=config.bind_host,
        port=config.bind_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
