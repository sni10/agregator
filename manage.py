#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    # A Windows console (cp1251) cannot show every name in headlines and photo credits: never fail on output
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors='replace')
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
