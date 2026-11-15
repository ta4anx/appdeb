#!/bin/sh
# Launcher installed as /usr/bin/appdeb by the Debian package.
PYTHONPATH=/usr/lib/appdeb${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec python3 -m appdeb "$@"
