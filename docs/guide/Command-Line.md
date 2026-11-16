# Command line

You do not need this chapter to use AppDeb. It is here for scripting, remote machines and
people who like terminals.

Every command understands the interface language:

```bash
LANGUAGE=de appdeb managers
```

## Show what is installed

```bash
appdeb list              # desktop id, name, kind, version
appdeb list --json       # same data as JSON
```

## See which package managers exist

```bash
appdeb managers
```

Output (tabs separate the columns):

```
Flatpak   available        1.16.6                8
Snap      available        2.77.1+ubuntu26.04.2  45
Homebrew  available        7.0.13                2
APT / dpkg available       1.23.7                2202
```

`appdeb managers --json` gives the same fields as objects.

## Search the stores

```bash
appdeb search calculator                 # all available managers
appdeb search ripgrep --manager brew     # one manager only
appdeb search calculator --json
```

## Install

From a file (the same formats the GUI accepts):

```bash
appdeb install ./Foo.AppImage --name Foo --icon foo.png
appdeb install ./program.tar.gz
appdeb install ./program.tar.gz --dest ~/Apps
appdeb install ./program.tar.gz --exec bin/program
appdeb install ./package.deb
appdeb install ./app.flatpakref
```

By name, from a manager:

```bash
appdeb install ripgrep --from brew
appdeb install gnome-calculator --from snap
appdeb install org.gnome.Calculator --from flatpak
```

Add `--yes` to skip the confirmation question. `--yes` never skips validation.

## Remove

```bash
appdeb remove appdeb-foo.desktop --yes
appdeb remove appdeb-foo.desktop --purge   # .deb: also remove configuration
```

## Desktop-entry doctor

```bash
appdeb scan             # list findings: severity, code, path, message
appdeb scan --json
appdeb fix-icons        # apply every automatic fix, print a summary
```

`fix-icons` exits with status 1 when problems remain that AppDeb cannot repair (for example a
menu entry pointing at a deleted program).

## GUI from the command line

```bash
appdeb                     # open the window
appdeb ./Foo.AppImage      # open the window on this file's install flow
```

On a machine without a graphical session, GUI commands print
`AppDeb: no graphical display; use a CLI subcommand` and exit with status 1.

Next: [Troubleshooting](Troubleshooting.md)
