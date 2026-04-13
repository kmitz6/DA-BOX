if [ -z "$TMUX" ]; then
  tmux attach-session -t main || tmux new-session -s main
fi
tmux send-keys -t main 'pgrep -f net-status.py >/dev/null || /usr/bin/python3 ~/Code/startup-status/net-status.py' C-m
