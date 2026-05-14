from __future__ import annotations
import argparse
import logging
import sys
import multiprocessing
from pathlib import Path

# ── Logging must be configured before any app imports ─────────────────────
from app.core.logging_config import setup_logging
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtWidgets import QApplication


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="abte",
        description="Abte productivity desktop app.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--debug",
        action="store_const",
        dest="log_level",
        const=logging.DEBUG,
        help="Show all debug output in console.",
    )
    group.add_argument(
        "--verbose",
        action="store_const",
        dest="log_level",
        const=logging.INFO,
        help="Show info-level output in console (default).",
    )
    group.add_argument(
        "--quiet",
        action="store_const",
        dest="log_level",
        const=logging.WARNING,
        help="Only show warnings and errors in console.",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run the test suite and exit.",
    )
    parser.set_defaults(log_level=logging.INFO)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if getattr(args, "run_tests", False):
        import subprocess
        print("Running test suite...\n")
        result = subprocess.run([sys.executable, "-m", "pytest","-v", "app/tests/test_abte.py"])
        return result.returncode

    _log_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    ) / "logs"
    setup_logging(log_dir=_log_dir, console_level=args.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Abte starting up. Console log level: %s", logging.getLevelName(args.log_level))

    from app.bootstrap import build_app  

    app = QApplication(sys.argv)
    QCoreApplication.setOrganizationName("zyroo")
    QCoreApplication.setApplicationName("abte")

    window, _services = build_app()
    
    def _cleanup():
        for name, service in _services.items():
            if hasattr(service, "stop"):
                try:
                    service.stop()
                except Exception as e:
                    logger.debug(f"Error stopping {name}: {e}")
            if hasattr(service, "wait"):
                try:
                    service.wait(500)
                except Exception as e:
                    logger.debug(f"Error waiting {name}: {e}")

    app.aboutToQuit.connect(_cleanup)
    window.show()

    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())