import click

from app.server import app


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    help="Transport type",
)
def main(transport: str):
    app.run(transport=transport)


if __name__ == "__main__":
    main()