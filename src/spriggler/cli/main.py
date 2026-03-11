"""Spriggler CLI — top-level entry point.

Usage:  spriggler <command> [subcommand] [options]

All commands support --help for detailed usage information.
"""

import click
from rich.console import Console

from spriggler.cli._style import BANNER, styled_header
from spriggler.cli.cmd_start import start
from spriggler.cli.cmd_stop import stop
from spriggler.cli.cmd_status import status
from spriggler.cli.cmd_calibrate import calibrate
from spriggler.cli.cmd_config import config
from spriggler.cli.cmd_sensor import sensor
from spriggler.cli.cmd_device import device
from spriggler.cli.cmd_log import log
from spriggler.cli.cmd_safety import safety
from spriggler.cli.cmd_physics import physics
from spriggler.cli.cmd_env import env

console = Console()


class SprigglerGroup(click.Group):
    """Custom group that shows the banner on --help."""

    def format_help(self, ctx, formatter):
        console.print(BANNER)
        console.print()
        super().format_help(ctx, formatter)


@click.group(cls=SprigglerGroup)
@click.version_option(package_name="spriggler", prog_name="spriggler")
@click.option(
    "--config-dir", "-c",
    type=click.Path(),
    default="~/.spriggler",
    envvar="SPRIGGLER_CONFIG_DIR",
    help="Configuration directory (default: ~/.spriggler, or $SPRIGGLER_CONFIG_DIR).",
)
@click.option(
    "--verbose", "-v",
    count=True,
    help="Increase verbosity (-v info, -vv debug).",
)
@click.option(
    "--quiet", "-q",
    is_flag=True,
    help="Suppress non-error output.",
)
@click.pass_context
def cli(ctx, config_dir, verbose, quiet):
    """Spriggler — physics-informed environmental controller.

    Controls temperature, humidity, and other properties across one or
    more enclosed environments using calibration-discovered device
    characteristics and physics-based reasoning.

    \b
    Quick start:
      spriggler config init          Create a starter config
      spriggler calibrate run        Calibrate devices and environments
      spriggler start                Start the controller daemon

    \b
    Getting help:
      spriggler --help               This message
      spriggler <command> --help     Help for a command group
      spriggler <cmd> <sub> --help   Help for a specific subcommand
    """
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet


# ── Register command groups ────────────────────────────────────────

cli.add_command(start)
cli.add_command(stop)
cli.add_command(status)
cli.add_command(calibrate)
cli.add_command(config)
cli.add_command(sensor)
cli.add_command(device)
cli.add_command(log)
cli.add_command(safety)
cli.add_command(physics)
cli.add_command(env)


if __name__ == "__main__":
    cli()
