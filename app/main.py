import os

import click
import uvicorn

from app.server import app, create_http_app


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="Transport type",
)
def main(transport: str):
    if transport == "streamable-http":
        uvicorn.run(create_http_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    else:
        app.run(transport=transport)


if __name__ == "__main__":
    main()
