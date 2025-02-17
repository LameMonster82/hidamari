import json
import subprocess
from pprint import pprint

import gi
import pydbus

gi.require_version("GnomeDesktop", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gio, GnomeDesktop, GLib, Wnck
from gi.repository.GdkPixbuf import Pixbuf
from commons import *


def is_gnome():
    """
    Check if current DE is GNOME or not.
    On Ubuntu 20.04, $XDG_CURRENT_DESKTOP = ubuntu:GNOME
    On Fedora 34, $XDG_CURRENT_DESKTOP = GNOME
    Hence we do the detection by looking for the word "gnome"
    """
    return "gnome" in os.environ["XDG_CURRENT_DESKTOP"].lower()


def is_wayland():
    """
    Check if current session is Wayland or not.
    $XDG_SESSION_TYPE = x11 | wayland
    """
    return os.environ["XDG_SESSION_TYPE"] == "wayland"


def list_local_video_dir():
    file_list = []
    ext_list = ["3g2", "3gp", "aaf", "asf", "avchd", "avi", "drc", "flv", "m2v", "m4p", "m4v", "mkv", "mng", "mov",
                "mp2", "mp4", "mpe", "mpeg", "mpg", "mpv", "mxf", "nsv", "ogg", "ogv", "qt", "rm", "rmvb", "roq", "svi",
                "vob", "webm", "wmv", "yuv"]
    for filename in os.listdir(VIDEO_WALLPAPER_DIR):
        filepath = os.path.join(VIDEO_WALLPAPER_DIR, filename)
        if os.path.isfile(filepath) and filename.split(".")[-1].lower() in ext_list:
            file_list.append(filepath)
    return sorted(file_list)


def xdg_open_video_dir():
    subprocess.call(["xdg-open", os.path.realpath(VIDEO_WALLPAPER_DIR)])


def generate_thumbnail_gnome(filename):
    factory = GnomeDesktop.DesktopThumbnailFactory()
    mtime = os.path.getmtime(filename)
    # Use Gio to determine the URI and mime type
    f = Gio.file_new_for_path(filename)
    uri = f.get_uri()
    info = f.query_info(
        "standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
    mime_type = info.get_content_type()

    if factory.lookup(uri, mtime) is not None:
        return False

    if not factory.can_thumbnail(uri, mime_type, mtime):
        return False

    thumbnail = factory.generate_thumbnail(uri, mime_type)
    if thumbnail is None:
        return False

    factory.save_thumbnail(thumbnail, uri, mtime)
    return True


def get_thumbnail_gnome(video_path, list_store, idx):
    file = Gio.File.new_for_path(video_path)
    info = file.query_info("*", 0, None)
    thumbnail = info.get_attribute_byte_string("thumbnail::path")
    if thumbnail is not None:
        new_pixbuf = Pixbuf.new_from_file_at_size(thumbnail, -1, 96)
        list_store[idx][0] = new_pixbuf
    else:
        generate_thumbnail_gnome(video_path)


def setup_autostart(autostart):
    if autostart:
        with open(AUTOSTART_DESKTOP_PATH, mode='w') as f:
            f.write(AUTOSTART_DESKTOP_CONTENT)
    else:
        try:
            os.remove(AUTOSTART_DESKTOP_PATH)
        except OSError:
            pass


class ActiveHandler:
    """
    Handler for monitoring screen lock
    """

    def __init__(self, on_active_changed: callable):
        session_bus = pydbus.SessionBus()
        screensaver_list = ["org.gnome.ScreenSaver",
                            "org.cinnamon.ScreenSaver",
                            "org.kde.screensaver",
                            "org.freedesktop.ScreenSaver"]
        for s in screensaver_list:
            try:
                proxy = session_bus.get(s)
                proxy.ActiveChanged.connect(on_active_changed)
            except GLib.Error:
                pass


class WindowHandler:
    """
    Handler for monitoring window events (maximized and fullscreen mode)
    """

    def __init__(self, on_window_state_changed: callable):
        self.on_window_state_changed = on_window_state_changed
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        self.screen.connect("window-opened", self.window_opened, None)
        self.screen.connect("window-closed", self.eval, None)
        self.screen.connect("active-workspace-changed", self.eval, None)
        for window in self.screen.get_windows():
            window.connect("state-changed", self.eval, None)

        self.prev_state = None
        # Initial check
        self.eval()

    def window_opened(self, screen, window, _):
        window.connect("state-changed", self.eval, None)

    def eval(self, *args):
        is_changed = False

        is_any_maximized, is_any_fullscreen = False, False
        for window in self.screen.get_windows():
            base_state = not Wnck.Window.is_minimized(window) and \
                         Wnck.Window.is_on_workspace(window, self.screen.get_active_workspace())
            window_name, is_maximized, is_fullscreen = window.get_name(), \
                                                       Wnck.Window.is_maximized(window) and base_state, \
                                                       Wnck.Window.is_fullscreen(window) and base_state
            if is_maximized is True:
                is_any_maximized = True
            if is_fullscreen is True:
                is_any_fullscreen = True

        cur_state = {"is_any_maximized": is_any_maximized, "is_any_fullscreen": is_any_fullscreen}
        if self.prev_state is None or self.prev_state != cur_state:
            is_changed = True
            self.prev_state = cur_state

        if is_changed:
            self.on_window_state_changed({"is_any_maximized": is_any_maximized, "is_any_fullscreen": is_any_fullscreen})
            print("WindowHandler:", cur_state)


class WindowHandlerGnome:
    """
    Handler for monitoring window events for Gnome only
    """

    def __init__(self, on_window_state_changed: callable):
        self.on_window_state_changed = on_window_state_changed
        self.gnome_shell = pydbus.SessionBus().get("org.gnome.Shell")
        self.prev_state = None
        GLib.timeout_add(500, self.eval)

    def eval(self):
        is_changed = False

        ret1, workspace = self.gnome_shell.Eval("""
                        global.workspace_manager.get_active_workspace_index()
                        """)

        ret2, maximized = self.gnome_shell.Eval(f"""
                var window_list = global.get_window_actors().find(window =>
                    window.meta_window.maximized_horizontally &
                    window.meta_window.maximized_vertically &
                    !window.meta_window.minimized &
                    window.meta_window.get_workspace().workspace_index == {workspace}
                );
                window_list
                """)

        ret3, fullscreen = self.gnome_shell.Eval(f"""
                var window_list = global.get_window_actors().find(window =>
                    window.meta_window.is_fullscreen() &
                    !window.meta_window.minimized &
                    window.meta_window.get_workspace().workspace_index == {workspace}
                );
                window_list
                """)
        if not all([ret1, ret2, ret3]):
            raise RuntimeError("Cannot communicate with Gnome Shell!")

        cur_state = {'is_any_maximized': maximized != "", 'is_any_fullscreen': fullscreen != ""}
        if self.prev_state is None or self.prev_state != cur_state:
            is_changed = True
            self.prev_state = cur_state

        if is_changed:
            self.on_window_state_changed({"is_any_maximized": maximized != "", "is_any_fullscreen": fullscreen != ""})
            print("WindowHandler:", cur_state)
        return True


class ConfigUtil:
    def _generate_template(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.save(CONFIG_TEMPLATE)

    def _check(self, config: dict):
        """Check if the config is valid"""
        is_all_keys_match = all(key in config for key in CONFIG_TEMPLATE)
        is_version_match = config.get("version") == CONFIG_VERSION
        return is_all_keys_match and is_version_match

    def load(self):
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                json_str = f.read()
                try:
                    config = json.loads(json_str)
                    if self._check(config):
                        print("Config JSON:")
                        pprint(config)
                        return config
                    else:
                        print("Config is invalid, generate new config")
                        self._generate_template()
                        return CONFIG_TEMPLATE
                except json.decoder.JSONDecodeError:
                    print("Config JSONDecodeError, generate new config")
                    self._generate_template()
                    return CONFIG_TEMPLATE
        else:
            print("Config not found, generate new config")
            self._generate_template()
            return CONFIG_TEMPLATE

    @staticmethod
    def save(config):
        print("Save config JSON")
        with open(CONFIG_PATH, "w") as f:
            json_str = json.dumps(config, indent=3)
            print(json_str, file=f)


if __name__ == "__main__":
    # Debug
    print(is_gnome())
    print(is_wayland())
