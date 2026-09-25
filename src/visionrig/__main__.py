from __future__ import annotations

import uvicorn

from .api import create_app
from .service_config import ServiceConfig


def main() -> None:
    config = ServiceConfig.from_env()
    bundle = config.build_bundle()
    app = create_app(
        bundle.pipeline,
        max_sensor_frame_bytes=config.max_sensor_frame_bytes,
    )
    uvicorn.run(app, host="127.0.0.1", port=8110, reload=False)


if __name__ == "__main__":
    main()
