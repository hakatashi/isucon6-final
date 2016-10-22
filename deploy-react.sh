#!/bin/bash
set -ex

USERNAME=$USER

curl -k -X POST --data-urlencode "payload={\"text\":\"$USERNAME started react deploy\"}" https://hooks.slack.com/services/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

for HOST in isu02 isu03 isu04; do
  ssh isucon@$HOST -i isucon6f.key "cd /home/isucon/webapp && git pull && sudo systemctl restart isu.service && sudo sysctl -p && curl -X POST --data-urlencode 'payload={\"text\":\"deploy to $HOST successfull\"}' https://hooks.slack.com/services/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"
done

curl -k -X POST --data-urlencode "payload={\"text\":\"$USERNAME ended react deploy\"}" https://hooks.slack.com/services/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
