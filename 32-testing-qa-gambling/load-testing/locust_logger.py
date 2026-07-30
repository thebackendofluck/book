# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import logging
import os


class PIVConsoleLogger(logging.Logger):
    def makeRecord(
        self,
        name,
        level,
        fn,
        lno,
        msg,
        args,
        exc_info,
        func=None,
        extra=None,
        sinfo=None,
    ):
        record = super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
        )
        # record.__dict__["extra_string"] = f" : {extra}" if extra else ""
        return record


logging.config.dictConfig(  # ty:ignore[possibly-missing-attribute]
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "[{levelname}] - {module} - <t-{thread}> <p-{process}>: {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
            },
        },
        "loggers": {
            "sportsbook": {
                "handlers": ["console"],
                "level": os.getenv("LOG_LEVEL", "INFO"),
                "propagate": True,
            }
        },
    }
)

logging.setLoggerClass(PIVConsoleLogger)

LOGGER = logging.getLogger("sportsbook")
