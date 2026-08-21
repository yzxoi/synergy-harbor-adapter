#!/bin/bash
cd /root/synergy-harbor-adapter
rm -rf jobs/synergy-deepswe-thinking-max
env -u SYNERGY_LINK_PROCESS_OWNER setsid nohup .venv/bin/pier run --config smoke/deepswe-synergy-thinking-max.yaml --env-file .env --job-name synergy-deepswe-thinking-max --yes > /tmp/deepswe-thinking-max.log 2>&1 < /dev/null &
echo launched_pid_$!
