#!/bin/sh
# =============================================================================
# install_linux_context_menu.sh - dev_helper "new*" scripts as file-manager
# context-menu (right-click) items on Linux.
#
# WHAT IT DOES
#   Adds every  dev_helper/new_file/new*.py  script to the right-click menu of:
#     nautilus (GNOME Files) / nemo (Cinnamon) / caja (MATE) -> "Scripts" submenu
#     dolphin  (KDE)                                       -> "Actions" submenu
#     thunar   (XFCE)                                      -> custom actions
#   A menu click opens a small terminal in the clicked/selected folder and runs
#   the script there. The scripts themselves take content from the clipboard,
#   so the usual flow is: copy an LLM answer -> right-click in your project
#   folder -> Scripts -> dev_helper -> new_file_from_clipboard_paste.
#   Install is idempotent: re-run this script any time to UPDATE (after
#   "git pull", after moving the repo, after adding new scripts).
#
# USAGE
#   ./install_linux_context_menu.sh                    # install/update, auto-detect file managers
#   ./install_linux_context_menu.sh install | update   # same as above
#   ./install_linux_context_menu.sh uninstall          # remove everything this script created
#   ./install_linux_context_menu.sh list               # show detected/installed state
#   Options:
#     --fm=nautilus,nemo,dolphin,caja,thunar | all | auto   (default: auto)
#     --python=/path/to/python3     interpreter baked into wrappers
#     --no-terminal                 menu items run silently, no terminal window
#     --repo=/path/to/dev_helper    repo location (default: folder of this script)
#     --system                      system-wide install (needs root; dolphin only - see below)
#
# HOW TO DO IT MANUALLY (what this script does under the hood)
#   Wrappers are generated into  ~/.local/share/dev_helper/context-menu/ :
#     dev-helper-run  - shared runner: finds target folder (first directory
#                       argument, else folder of first file argument, else CWD),
#                       opens a terminal there, runs the script in it.
#     new_audio, new_img, ...        - one tiny launcher per script.
#
#   * nautilus / nemo / caja ("Scripts" menu; per-user by design):
#       Any executable file in  ~/.local/share/<file-manager>/scripts/  shows up
#       in right-click -> Scripts. The file manager runs it with CWD = clicked
#       folder and selected items as arguments. Subfolders become submenus.
#         mkdir -p ~/.local/share/nautilus/scripts/dev_helper
#         cp ~/.local/share/dev_helper/context-menu/new_audio \
#            ~/.local/share/nautilus/scripts/dev_helper/
#         chmod +x ~/.local/share/nautilus/scripts/dev_helper/new_audio
#       (use ~/.local/share/nemo/scripts resp. ~/.local/share/caja/scripts for
#       those managers; restart the file manager if the menu does not refresh)
#
#   * dolphin (service menus):
#       Drop a .desktop file into ~/.local/share/kio/servicemenus/
#       (Plasma 6 and Plasma 5 >= 5.85) or ~/.local/share/kservices5/ServiceMenus/
#       (older Plasma 5; that variant needs the extra key
#        ServiceTypes=KonqPopupMenu/Plugin). chmod +x the .desktop, run
#       "kbuildsycoca6" (or kbuildsycoca5) and restart Dolphin. Template:
#         [Desktop Entry]
#         Type=Service
#         MimeType=inode/directory;
#         Actions=new-audio
#         X-KDE-Submenu=dev_helper
#         [Desktop Action new-audio]
#         Name=new_audio
#         Exec=/home/you/.local/share/dev_helper/context-menu/dev-helper-run new_audio.py %f
#       System-wide location (what --system uses): /usr/share/kio/servicemenus/
#
#   * thunar (custom actions; per-user only):
#       GUI: Edit -> Configure custom actions... -> "+" ->
#            Name: new_audio
#            Command: /home/you/.local/share/dev_helper/context-menu/dev-helper-run new_audio.py %f
#            Appearance conditions: File pattern *, tick "Directories" (also
#            makes it appear on right-click of the folder background).
#       Text mode: the same data lives in ~/.config/Thunar/uca.xml. Close Thunar
#       ("thunar -q") first, or it will overwrite the file on exit.
#
# UNINSTALL MANUALLY
#   Remove ~/.local/share/dev_helper/context-menu/, the dev_helper/ subfolder in
#   each ~/.local/share/<fm>/scripts/, ~/.local/share/kio/servicemenus/
#   dev-helper-new-files.desktop (+ kservices5/ServiceMenus variant) and the
#   matching <action> entries in ~/.config/Thunar/uca.xml - or just run:
#   ./install_linux_context_menu.sh uninstall
# =============================================================================

PROG=install_linux_context_menu.sh
MODE=install
FMS=auto
SYSTEM=0
USE_TERMINAL=1
PYTHON=""
REPO=""
SAVED_ARGS="$@"

ALL_FMS="nautilus nemo caja dolphin thunar"

# absolute path to this script (safe for sudo re-exec and usage output)
case "$0" in
    */*) SELF_ABS=$(cd -P "$(dirname -- "$0")" && pwd)/$(basename -- "$0") ;;
    *)   SELF_ABS=$(command -v "$0" 2>/dev/null || printf '%s' "$0") ;;
esac

usage() {
    awk 'NR>1 && /^# =+$/ {c++; if (c==2) exit} NR>1 {sub(/^# \{0,1\}/, ""); print}' "$SELF_ABS"
}
info()  { printf '  [ok] %s\n' "$*"; }
warn()  { printf '  [!]  %s\n' "$*" >&2; }
die()   { printf '  ERROR: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        install|update)    MODE=install ;;
        uninstall|remove)  MODE=uninstall ;;
        list)              MODE=list ;;
        --fm=*)            FMS=${1#--fm=} ;;
        --python=*)        PYTHON=${1#--python=} ;;
        --repo=*)          REPO=${1#--repo=} ;;
        --no-terminal)     USE_TERMINAL=0 ;;
        --system)          SYSTEM=1 ;;
        -h|--help)         usage; exit 0 ;;
        *)                 printf 'unknown argument: %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

# --- locate the repo (this script sits in the repo root) ----------------------
if [ -z "$REPO" ]; then
    case "$0" in
        */*) self=$0 ;;
        *)   self=$(command -v "$0" 2>/dev/null || printf '%s' "$0") ;;
    esac
    REPO=$(cd -P "$(dirname -- "$self")" 2>/dev/null && pwd) || REPO=""
fi

REPO_OK=0
if [ -n "$REPO" ] && [ -d "$REPO/dev_helper/new_file" ] && [ -d "$REPO/path_args" ]; then
    REPO_OK=1
fi
if [ "$REPO_OK" -eq 0 ]; then
    [ "$MODE" = "uninstall" ] || die "dev_helper repo not found at '${REPO:-?}' (need dev_helper/new_file and path_args; use --repo=/path/to/dev_helper)"
    warn "repo not found at '${REPO:-?}' - uninstall will clean up generated files only"
fi

# --- the scripts we expose ----------------------------------------------------
SCRIPTS=""
if [ "$REPO_OK" -eq 1 ]; then
    for f in "$REPO"/dev_helper/new_file/new*.py; do
        [ -f "$f" ] || continue
        SCRIPTS="$SCRIPTS $(basename -- "$f")"
    done
    [ -n "$SCRIPTS" ] || [ "$MODE" = "uninstall" ] || die "no new*.py scripts in $REPO/dev_helper/new_file"
fi

# --- paths --------------------------------------------------------------------
if [ "$SYSTEM" -eq 1 ]; then
    if [ "$(id -u)" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 || die "--system needs root; re-run with sudo"
        printf '  re-running with sudo for --system ...\n'
        exec sudo "$SELF_ABS" $SAVED_ARGS
    fi
    BASE=/usr/local/share/dev_helper/context-menu
else
    BASE=${XDG_DATA_HOME:-$HOME/.local/share}/dev_helper/context-menu
    if [ "$(id -u)" -eq 0 ]; then
        warn "running as root without --system: installing for root's HOME only"
    fi
fi
XDG_DATA=${XDG_DATA_HOME:-$HOME/.local/share}
XDG_CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
DOLPHIN_DESKTOP_NAME=dev-helper-new-files.desktop

# --- helpers ------------------------------------------------------------------
xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

icon_for() {
    case "$1" in
        new_audio*)          echo audio-x-generic ;;
        new_voice*)          echo audio-input-microphone ;;
        new_img*|new_animated_webP*) echo image-x-generic ;;
        *HTLM*|*HTML*|*html*) echo text-html ;;
        *)                   echo text-x-generic ;;
    esac
}

desc_for() {
    case "$1" in
        new_audio.py)                      echo "Generate numbered sine-wave audio files (wav/opus)" ;;
        new_audio_sweep.py)                echo "Generate frequency sweep audio files" ;;
        new_file_from_clipboard_paste.py)  echo "Save clipboard (LLM answer / code) as files with proper extensions" ;;
        new_file_from_clipboard_lite.py)   echo "Save clipboard text as a file with detected extension (lite)" ;;
        new_file_from_LLM.py)              echo "Chat with a local LLM and save produced files" ;;
        new_img.py)                        echo "Create numbered placeholder color images" ;;
        new_animated_webP_from_files.py)   echo "Create an animated WebP from image files" ;;
        new_voice.py)                      echo "Create numbered speech (TTS) files" ;;
        new_HTLM_from_clipboard_paste.py)  echo "Save clipboard rich text as HTML file" ;;
        *)                                 echo "dev_helper new-file script" ;;
    esac
}

# echo the terminal-launch prefix for a known emulator name, fail if unknown
known_term() {
    case "$1" in
        gnome-terminal)  echo 'gnome-terminal --' ;;
        konsole)         echo 'konsole -e' ;;
        xfce4-terminal)  echo 'xfce4-terminal -x' ;;
        mate-terminal)   echo 'mate-terminal -x' ;;
        qterminal)       echo 'qterminal -e' ;;
        kitty)           echo 'kitty' ;;
        alacritty)       echo 'alacritty -e' ;;
        foot)            echo 'foot' ;;
        wezterm)         echo 'wezterm start --' ;;
        kgx)             echo 'kgx --' ;;
        st)              echo 'st' ;;
        xterm)           echo 'xterm -e' ;;
        uxterm)          echo 'uxterm -e' ;;
        *)               return 1 ;;
    esac
}

TERM_LAUNCH=""
detect_terminal() {
    [ "$USE_TERMINAL" -eq 1 ] || return 0
    if [ -n "$TERMINAL" ]; then
        t=$(basename -- "$TERMINAL" 2>/dev/null || printf '%s' "$TERMINAL")
        if p=$(known_term "$t") && command -v "$t" >/dev/null 2>&1; then
            TERM_LAUNCH=$p; return 0
        fi
        warn "TERMINAL='$TERMINAL' is not a known emulator - ignoring"
    fi
    for t in gnome-terminal konsole xfce4-terminal mate-terminal qterminal \
             kitty alacritty foot wezterm kgx st xterm uxterm; do
        command -v "$t" >/dev/null 2>&1 || continue
        TERM_LAUNCH=$(known_term "$t")
        return 0
    done
    return 1
}

fm_present() {
    command -v "$1" >/dev/null 2>&1 && return 0
    case "$1" in
        nautilus) [ -d "$XDG_DATA/nautilus" ] ;;
        nemo)     [ -d "$XDG_DATA/nemo" ] ;;
        caja)     [ -d "$XDG_DATA/caja" ] ;;
        dolphin)  [ -d "$XDG_DATA/kio" ] ;;
        thunar)   [ -d "$XDG_CONFIG/Thunar" ] ;;
        *)        false ;;
    esac
}

fm_nice() {
    case "$1" in
        nautilus) printf 'nautilus (GNOME Files)' ;;
        nemo)     printf 'nemo (Cinnamon)' ;;
        caja)     printf 'caja (MATE)' ;;
        dolphin)  printf 'dolphin (KDE)' ;;
        thunar)   printf 'thunar (XFCE)' ;;
    esac
}

FMS_RESOLVED=""
resolve_fms() {
    case "$FMS" in
        auto)
            for fm in $ALL_FMS; do
                fm_present "$fm" && FMS_RESOLVED="$FMS_RESOLVED $fm"
            done
            [ -n "$FMS_RESOLVED" ] || die "no supported file manager detected (nautilus/nemo/caja/dolphin/thunar); use e.g. --fm=dolphin or --fm=all"
            ;;
        all)
            FMS_RESOLVED=$ALL_FMS
            ;;
        *)
            for fm in $(printf '%s' "$FMS" | tr ',' ' '); do
                case " $ALL_FMS " in
                    *" $fm "*) FMS_RESOLVED="$FMS_RESOLVED $fm" ;;
                    *) die "unknown file manager '$fm' in --fm (known: $ALL_FMS)" ;;
                esac
            done
            [ -n "$FMS_RESOLVED" ] || die "empty --fm list"
            ;;
    esac
}

# --- generated files ----------------------------------------------------------

write_runner() {
    mkdir -p "$BASE" || die "cannot create $BASE"
    {
        printf '#!/bin/sh\n'
        printf '# dev_helper context-menu runner - generated by %s on %s\n' "$PROG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '# Re-run the installer to update or uninstall.\n'
        printf '# Usage: dev-helper-run <script.py> [paths...]\n'
        printf '# Target folder = first directory argument, else folder of first file\n'
        printf '# argument, else current working directory (the clicked folder).\n'
        printf "REPO='%s'\n" "$REPO"
        printf "PYTHON='%s'\n" "$PYTHON"
        printf "TERMINAL_LAUNCH='%s'\n" "$TERM_LAUNCH"
        cat <<'RUNNER_EOF'

export PYTHONPATH="$REPO:$REPO/path_args${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

name=${1:-}
case "$name" in
    ''|*[!A-Za-z0-9._-]*) echo "dev-helper-run: bad script name: $name" >&2; exit 2 ;;
esac
script_path="$REPO/dev_helper/new_file/$name"
[ -f "$script_path" ] || { echo "dev-helper-run: script not found: $script_path" >&2; exit 2; }
shift

target="$PWD"
have_dir=0
for a in "$@"; do
    if [ -d "$a" ]; then target="$a"; have_dir=1; break; fi
done
if [ "$have_dir" -eq 0 ] && [ "$#" -gt 0 ] && [ -f "$1" ]; then
    target=$(dirname -- "$1")
fi

# runs inside the terminal window: $1=dir $2=python $3=script $4=name $5=repo
inner='
export PYTHONPATH="$5:$5/path_args${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
cd -- "$1" || exit 1
"$2" "$3"
rc=$?
printf "\n[dev_helper] %s exited with code %s.\nPress ENTER to close this window.\n" "$4" "$rc"
IFS= read -r _
exit "$rc"
'

if [ -n "$TERMINAL_LAUNCH" ] && [ "${DHF_NO_TERM:-0}" != "1" ]; then
    # shellcheck disable=SC2086
    eval "set -- $TERMINAL_LAUNCH"
    exec "$@" sh -c "$inner" dev-helper-run "$target" "$PYTHON" "$script_path" "$name" "$REPO"
else
    cd -- "$target" || exit 1
    exec "$PYTHON" "$script_path"
fi
RUNNER_EOF
    } > "$BASE/dev-helper-run" || die "cannot write $BASE/dev-helper-run"
    chmod 755 "$BASE/dev-helper-run"
    if [ "$SYSTEM" -eq 1 ]; then m=system; else m=user; fi
    {
        printf 'repo=%s\nmode=%s\ninstalled=%s\n' "$REPO" "$m" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$BASE/.dev-helper-context-menu"
}

write_launcher() {  # $1 = script file name, e.g. new_audio.py
    n=${1%.py}
    {
        printf '#!/bin/sh\n'
        printf '# dev_helper context-menu item: %s (runs dev-helper-run %s)\n' "$n" "$1"
        printf '# Generated by %s - re-run it to update, "uninstall" removes this file.\n' "$PROG"
        printf "exec '%s' '%s' \"\$@\"\n" "$BASE/dev-helper-run" "$1"
    } > "$BASE/$n" || die "cannot write $BASE/$n"
    chmod 755 "$BASE/$n"
}

# remove stale launchers in $1 (files starting with "new" not in current list)
clean_stale() {  # $1 = directory
    [ -d "$1" ] || return 0
    for f in "$1"/new*; do
        [ -f "$f" ] || continue
        n=$(basename -- "$f")
        keep=0
        for s in $SCRIPTS; do [ "${s%.py}" = "$n" ] && keep=1; done
        [ "$keep" -eq 1 ] || rm -f "$f"
    done
}

write_base() {
    for s in $SCRIPTS; do write_launcher "$s"; done
    clean_stale "$BASE"
    info "runner + $(printf '%s\n' $SCRIPTS | wc -l | tr -d ' ') launchers -> $BASE"
}

# --- nautilus / nemo / caja ---------------------------------------------------

install_scripts_fm() {  # $1 = fm name
    dst=$XDG_DATA/$1/scripts/dev_helper
    mkdir -p "$dst" || die "cannot create $dst"
    for s in $SCRIPTS; do
        cp "$BASE/${s%.py}" "$dst/${s%.py}" && chmod 755 "$dst/${s%.py}" \
            || die "cannot install into $dst"
    done
    clean_stale "$dst"
    info "$(fm_nice "$1"): right-click -> Scripts -> dev_helper  ($dst)"
}

uninstall_scripts_fm() {  # $1 = fm name
    dst=$XDG_DATA/$1/scripts/dev_helper
    [ -d "$dst" ] || return 0
    n=0
    for f in "$dst"/new*; do
        [ -f "$f" ] || continue
        rm -f "$f" && n=$((n + 1))
    done
    rmdir "$dst" 2>/dev/null || warn "$dst not empty - leftover files kept"
    [ "$n" -gt 0 ] && info "$(fm_nice "$1"): removed $n script entry(ies)"
    return 0
}

# --- dolphin ------------------------------------------------------------------

write_dolphin_desktop() {  # $1 = output path, $2 = legacy (1 adds ServiceTypes keys)
    actions=""
    for s in $SCRIPTS; do
        id=$(printf '%s' "${s%.py}" | tr '_' '-')
        actions="${actions:+$actions;}$id"
    done
    {
        printf '[Desktop Entry]\n'
        printf 'Type=Service\n'
        printf 'MimeType=inode/directory;\n'
        printf 'Actions=%s\n' "$actions"
        printf 'X-KDE-Submenu=dev_helper\n'
        printf 'Icon=document-new\n'
        if [ "$2" -eq 1 ]; then
            printf 'ServiceTypes=KonqPopupMenu/Plugin\n'
            printf 'X-KDE-ServiceTypes=KonqPopupMenu/Plugin\n'
        fi
        for s in $SCRIPTS; do
            id=$(printf '%s' "${s%.py}" | tr '_' '-')
            printf '\n[Desktop Action %s]\n' "$id"
            printf 'Name=%s\n' "${s%.py}"
            printf 'Icon=%s\n' "$(icon_for "$s")"
            printf 'Exec="%s" %s %%f\n' "$BASE/dev-helper-run" "$s"
        done
    } > "$1" || die "cannot write $1"
    chmod 755 "$1"
}

kbuild_refresh() {
    if command -v kbuildsycoca6 >/dev/null 2>&1; then kbuildsycoca6 >/dev/null 2>&1
    elif command -v kbuildsycoca5 >/dev/null 2>&1; then kbuildsycoca5 >/dev/null 2>&1
    fi
}

install_dolphin() {  # $1 = servicemenus dir (must exist)
    write_dolphin_desktop "$1/$DOLPHIN_DESKTOP_NAME" 0
    info "dolphin (KDE): right-click a folder or empty space -> Actions -> dev_helper  ($1)"
}

install_dolphin_legacy() {  # $1 = legacy ServiceMenus dir (must exist)
    write_dolphin_desktop "$1/$DOLPHIN_DESKTOP_NAME" 1
    info "dolphin legacy service-menu path also installed ($1)"
}

uninstall_dolphin_dir() {  # $1 = servicemenus dir
    [ -f "$1/$DOLPHIN_DESKTOP_NAME" ] || return 0
    rm -f "$1/$DOLPHIN_DESKTOP_NAME" && info "dolphin: removed $1/$DOLPHIN_DESKTOP_NAME"
}

# --- thunar -------------------------------------------------------------------

thunar_uca=$XDG_CONFIG/Thunar/uca.xml

thunar_block_file() {  # writes our <action> entries to a temp file, echoes path
    tmp=${TMPDIR:-/tmp}/dev-helper-uca.$$
    : > "$tmp"
    for s in $SCRIPTS; do
        cmd="\"$BASE/dev-helper-run\" $s \"%f\""
        {
            printf '\t<action>\n'
            printf '\t\t<icon>%s</icon>\n' "$(icon_for "$s")"
            printf '\t\t<name>%s</name>\n' "$(xml_escape "${s%.py}")"
            printf '\t\t<command>%s</command>\n' "$(xml_escape "$cmd")"
            printf '\t\t<description>%s</description>\n' "$(xml_escape "$(desc_for "$s")")"
            printf '\t\t<patterns>*</patterns>\n'
            printf '\t\t<directories/>\n'
            printf '\t</action>\n'
        } >> "$tmp"
    done
    printf '%s' "$tmp"
}

thunar_remove_ours() {  # $1 = uca.xml, $2 = tmp out; drops <action> blocks that call our runner
    awk -v base="$BASE/dev-helper-run" '
        /<action>/ {
            if ($0 ~ /<\/action>/) {
                if (index($0, base) == 0) print
                next
            }
            buf = $0; hold = 1; next
        }
        hold {
            buf = buf "\n" $0
            if ($0 ~ /<\/action>/) {
                hold = 0
                if (index(buf, base) == 0) print buf
            }
            next
        }
        { print }
    ' "$1" > "$2"
}

install_thunar() {
    if command -v pgrep >/dev/null 2>&1 && pgrep -x thunar >/dev/null 2>&1; then
        warn "Thunar is running and will overwrite uca.xml on exit:"
        warn "run 'thunar -q', then re-run this installer to be safe"
    fi
    mkdir -p "$XDG_CONFIG/Thunar" || die "cannot create $XDG_CONFIG/Thunar"
    block=$(thunar_block_file)
    if [ ! -f "$thunar_uca" ]; then
        {
            printf '<?xml version="1.0" encoding="UTF-8"?>\n'
            printf '<actions>\n'
            cat "$block"
            printf '</actions>\n'
        } > "$thunar_uca" || { rm -f "$block"; die "cannot write $thunar_uca"; }
    else
        [ -f "$thunar_uca.dev-helper.bak" ] || cp "$thunar_uca" "$thunar_uca.dev-helper.bak"
        thunar_remove_ours "$thunar_uca" "$thunar_uca.tmp.$$" \
            || { rm -f "$block" "$thunar_uca.tmp.$$"; die "cannot parse $thunar_uca - edit manually"; }
        grep -q '</actions>' "$thunar_uca.tmp.$$" \
            || { rm -f "$block" "$thunar_uca.tmp.$$"; die "no </actions> found in $thunar_uca - edit manually"; }
        awk -v blockf="$block" '
            /<\/actions>/ && !ins { while ((getline line < blockf) > 0) print line; ins = 1 }
            { print }
        ' "$thunar_uca.tmp.$$" > "$thunar_uca.new.$$" \
            || { rm -f "$block" "$thunar_uca.tmp.$$" "$thunar_uca.new.$$"; die "cannot update $thunar_uca"; }
        mv "$thunar_uca.new.$$" "$thunar_uca"
        rm -f "$thunar_uca.tmp.$$"
    fi
    rm -f "$block"
    info "thunar (XFCE): right-click a folder or background -> custom actions  ($thunar_uca)"
}

uninstall_thunar() {
    [ -f "$thunar_uca" ] || return 0
    thunar_remove_ours "$thunar_uca" "$thunar_uca.tmp.$$" \
        || { rm -f "$thunar_uca.tmp.$$"; die "cannot parse $thunar_uca"; }
    mv "$thunar_uca.tmp.$$" "$thunar_uca"
    info "thunar (XFCE): removed custom action entries from $thunar_uca"
    [ -f "$thunar_uca.dev-helper.bak" ] && \
        info "backup kept at $thunar_uca.dev-helper.bak (delete it if not needed)"
}

# --- per-mode drivers ---------------------------------------------------------

install_user() {
    # pre-validate before touching anything
    case " $FMS_RESOLVED " in
        *" thunar "*)
            if [ -f "$thunar_uca" ] && ! grep -q '</actions>' "$thunar_uca"; then
                die "$thunar_uca looks broken (no </actions>) - fix or delete it first"
            fi
            ;;
    esac
    write_runner
    write_base
    for fm in $FMS_RESOLVED; do
        case "$fm" in
            nautilus|nemo|caja) install_scripts_fm "$fm" ;;
            dolphin)
                d=$XDG_DATA/kio/servicemenus
                mkdir -p "$d" || die "cannot create $d"
                install_dolphin "$d"
                legacy=$XDG_DATA/kservices5/ServiceMenus
                if [ -d "$legacy" ]; then
                    install_dolphin_legacy "$legacy"
                else
                    [ -d /usr/share/kservices5 ] && \
                        warn "old Plasma 5 detected: if entries do not show, also copy the .desktop to $legacy (see header)"
                fi
                ;;
            thunar) install_thunar ;;
        esac
    done
    kbuild_refresh
}

install_system() {
    case " $FMS_RESOLVED " in
        *" dolphin "*) : ;;
        *) FMS_RESOLVED="$FMS_RESOLVED dolphin" ;;
    esac
    write_runner
    write_base
    d=/usr/share/kio/servicemenus
    mkdir -p "$d" || die "cannot create $d"
    install_dolphin "$d"
    [ -d /usr/share/kservices5/ServiceMenus ] && install_dolphin_legacy /usr/share/kservices5/ServiceMenus
    kbuild_refresh
    warn "nautilus/nemo/caja/thunar have NO system-wide mechanism - run without --system (per-user) for those"
    case "$REPO" in
        /home/*) warn "$REPO is under /home - make sure it is readable by all users for a system-wide install" ;;
    esac
}

uninstall_user() {
    # uninstall is thorough: clean every manager, not only selected/detected
    if [ -d "$BASE" ]; then
        if [ -f "$BASE/.dev-helper-context-menu" ]; then
            rm -rf "$BASE" && info "removed $BASE"
            rmdir "$(dirname -- "$BASE")" 2>/dev/null
        else
            warn "refusing to remove $BASE (no marker file inside) - remove it manually"
        fi
    fi
    for fm in nautilus nemo caja; do uninstall_scripts_fm "$fm"; done
    uninstall_dolphin_dir "$XDG_DATA/kio/servicemenus"
    uninstall_dolphin_dir "$XDG_DATA/kservices5/ServiceMenus"
    uninstall_thunar
    kbuild_refresh
}

uninstall_system() {
    if [ -d "$BASE" ]; then
        if [ -f "$BASE/.dev-helper-context-menu" ]; then
            rm -rf "$BASE" && info "removed $BASE"
        else
            warn "refusing to remove $BASE (no marker file inside)"
        fi
    fi
    uninstall_dolphin_dir /usr/share/kio/servicemenus
    uninstall_dolphin_dir /usr/share/kservices5/ServiceMenus
    kbuild_refresh
}

show_list() {
    printf 'repo: %s\n' "$REPO"
    [ "$REPO_OK" -eq 1 ] && printf 'scripts: %s\n' "$(printf '%s\n' $SCRIPTS | tr '\n' ' ')"
    printf 'wrapper dir: %s%s\n' "$BASE" "$( [ -d "$BASE" ] && printf ' [installed]' )"
    if detect_terminal; then
        printf 'terminal for menu windows: %s\n' "$TERM_LAUNCH"
    else
        printf 'terminal for menu windows: none found (install one, or xterm; or use --no-terminal)\n'
    fi
    printf 'detected file managers:'
    found=0
    for fm in $ALL_FMS; do fm_present "$fm" && { printf ' %s' "$fm"; found=1; }; done
    [ "$found" -eq 1 ] || printf ' none'
    printf '\n'
    for fm in nautilus nemo caja; do
        d=$XDG_DATA/$fm/scripts/dev_helper
        [ -d "$d" ] && printf '  %-8s installed: %s item(s) in %s\n' "$fm" "$(ls -1 "$d" 2>/dev/null | wc -l | tr -d ' ')" "$d"
    done
    [ -f "$XDG_DATA/kio/servicemenus/$DOLPHIN_DESKTOP_NAME" ] && printf '  dolphin  installed: %s\n' "$XDG_DATA/kio/servicemenus/$DOLPHIN_DESKTOP_NAME"
    [ -f "$XDG_DATA/kservices5/ServiceMenus/$DOLPHIN_DESKTOP_NAME" ] && printf '  dolphin  installed (legacy): %s\n' "$XDG_DATA/kservices5/ServiceMenus/$DOLPHIN_DESKTOP_NAME"
    [ -f "$thunar_uca" ] && grep -q "dev-helper-run" "$thunar_uca" 2>/dev/null && printf '  thunar   installed: entries in %s\n' "$thunar_uca"
    return 0
}

# --- main ---------------------------------------------------------------------

case "$MODE" in
    list)
        show_list
        exit 0
        ;;
    install)
        [ "$REPO_OK" -eq 1 ] || die "repo not found"
        case "$REPO$BASE$PYTHON" in *\'*) die "paths with single quotes are not supported" ;; esac
        if [ -z "$PYTHON" ]; then
            PYTHON=$(command -v python3 || command -v python) || die "python3 not found (use --python=/path/to/python3)"
        fi
        [ -x "$PYTHON" ] || die "python not executable: $PYTHON"
        detect_terminal || warn "no known terminal emulator found - items will run without a window (no output, no prompts); install one (e.g. xterm) and re-run, or pass --no-terminal"
        [ "$SYSTEM" -eq 0 ] && resolve_fms
        printf '== dev_helper context-menu install ==\n'
        printf 'repo: %s\npython: %s\nterminal: %s\n' "$REPO" "$PYTHON" "${TERM_LAUNCH:-none (silent)}"
        if [ "$SYSTEM" -eq 1 ]; then
            install_system
        else
            install_user
        fi
        printf '\nDone. If menus do not refresh: nautilus -q / nemo -q / caja -q, restart dolphin, thunar -q.\n'
        printf 'Uninstall anytime with: ./%s uninstall\n' "$PROG"
        ;;
    uninstall)
        printf '== dev_helper context-menu uninstall ==\n'
        if [ "$SYSTEM" -eq 1 ]; then
            uninstall_system
        else
            [ -d /usr/share/kio/servicemenus ] && [ -f /usr/share/kio/servicemenus/$DOLPHIN_DESKTOP_NAME ] && \
                warn "system-wide dolphin entry exists - run 'sudo ./$PROG uninstall --system' to remove it too"
            uninstall_user
        fi
        printf 'Done.\n'
        ;;
esac
