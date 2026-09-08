import json
from pathlib import Path

from sophie_bot.config import CONFIG
from sophie_bot.modules import assemble_api_modules, discover_modules
from sophie_bot.services.rest import create_app
from sophie_bot.utils.logger import log


def generate_openapi() -> None:
    """Generate the API schema from import-only module metadata."""
    log.info("Starting OpenAPI generation task...")
    registry = discover_modules(["*"], CONFIG.modules_not_load)
    app = create_app(CONFIG)
    assemble_api_modules(app, registry)
    openapi_data = app.openapi()

    output_path = Path("openapi.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(openapi_data, output_file, indent=2)

    log.info(f"OpenAPI documentation generated to {output_path}")


if __name__ == "__main__":
    generate_openapi()
