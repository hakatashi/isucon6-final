#!/bin/bash
set -ex

USERNAME=$USER

curl -k -X POST --data-urlencode "payload={\"text\":\"$USERNAME started deploy\"}" https://hooks.slack.com/services/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

for HOST in isu02 isu03 isu04 isu05; do
  ssh isucon@$HOST -i isucon6f.key "cd /home/isucon/webapp && git pull && sudo docker-compose -f docker-compose.yml build && sudo systemctl restart isu.service && sudo sysctl -p && curl -X POST --data-urlencode \"payload={\\\"text\\\":\\\"deploy to $HOST successful (\$(git rev-parse HEAD))\\\"}\" https://hooks.slack.com/services/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"
done

curl -k -X POST --data-urlencode "payload={\"text\":\"$USERNAME ended deploy\"}" https://hooks.slack.com/services/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
