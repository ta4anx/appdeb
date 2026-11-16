# System and resources

The **System** page is a small system monitor. It updates every two seconds while you are
looking at it (interval configurable in **Settings → Monitoring**).

## What each row means

| Row | Plain meaning |
| --- | --- |
| **CPU** | How busy the processor is overall, with a small line chart of the last minute |
| **Memory** | Used / total RAM and the percentage |
| **Swap** | Disk space used as overflow memory when RAM is full |
| **Home disk** | Space used in your home folder |
| **Network** | Current download and upload speed |
| **Load average** | How many tasks were waiting for the CPU (1, 5 and 15 minute averages) |
| **Uptime** | How long since the computer was started |
| **Top processes** | The five busiest programs right now, with CPU % and memory |

Nothing here changes your system; it is information only. To stop a program that is stuck, go
to **Installed → the app → End processes**, which asks for confirmation first.

## Per-app resources

Every app's details page has its own **Resources** group: CPU, memory, threads, process count,
disk read/write and start time. This is the quickest way to answer "what is eating my
battery?".

- For **Flatpak** apps, AppDeb finds processes by their sandbox ID.
- For **AppImage** apps, it matches the bundle path and the temporary mount name.
- For native and `.deb` apps, it matches the executable name from the menu entry.

If an app was started indirectly (from a script, for example), the numbers can stay at zero —
that is a limitation, not a bug.

Next: [Languages](Languages.md)
