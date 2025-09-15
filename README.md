# DA BOX
Early draft, meant to be a Raspberry Pi4-based network testing utility with a twist that it can pretty much test every kind of popular IEEEs: 802.3, 802.11, 802.15.1, 802.15.4 etc


# Shared tmux Session Setup
Raspberry Pi has GPIO 3.5inch screen so session can be shared with remote ssh (seeing keystrokes live on screen, not mandatory)

## Configuration (`~/.bash_profile`)
```
if [ -z "$TMUX" ]; then
  tmux attach-session -t main || tmux new-session -s main
fi
tmux send-keys -t main 'pgrep -f net-status.py >/dev/null || /usr/bin/python3 /~/net-status.py' C-m
```

##Setup Steps

    Install tmux:

      sudo apt install tmux

    Add the configuration snippet to ~/.bash_profile

    Ensure .bash_profile is sourced on login (add to .bashrc if needed)

    Reboot or log out/in to test the shared session
