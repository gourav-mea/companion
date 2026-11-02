#!/usr/bin/env python3
# Unplugged AI companion - entry point.
#
# Logic now lives in the companion/ package (config, audio, speech, llm,
# skills, conversation, web, cli). This file just launches it so the old
# `python3 talk.py [doctor|selftest|smoke-memory|metrics|--text ...]` usage keeps working.
#
# Face: open http://localhost:8080/face.html  (served automatically)

from companion.app import run_cli

if __name__ == "__main__":
    run_cli()
