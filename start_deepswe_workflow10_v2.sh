#!/bin/bash
cd /root/synergy-harbor-adapter
rm -rf jobs/synergy-deepswe-thinking-max-workflow10-v2
env -u SYNERGY_LINK_PROCESS_OWNER setsid nohup .venv/bin/pier run --config smoke/deepswe-synergy-thinking-max-workflow10-v2.yaml --env-file .env --job-name synergy-deepswe-thinking-max-workflow10-v2 --yes > /tmp/deepswe-workflow10-v2.log 2>&1 < /dev/null &
echo launched_pid_$!
