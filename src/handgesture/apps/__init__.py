"""Applications that run inside the spatial interface's windows."""

from .base import ActionResult, App, OSRequest, available_apps, create_app, register
from .browser import BrowserApp, Tab
from .files import FilesApp, PathOutsideRoot
from .keyboard import Key, KeyboardApp, KeyboardConfig, KeyPress, Layout
from .media import MusicApp, Repeat, Track, VideoApp
from .settings import SETTINGS, SettingsApp
from .viewer import Image, ViewerApp

__all__ = [
    "SETTINGS",
    "ActionResult",
    "App",
    "BrowserApp",
    "FilesApp",
    "Image",
    "Key",
    "KeyPress",
    "KeyboardApp",
    "KeyboardConfig",
    "Layout",
    "MusicApp",
    "OSRequest",
    "PathOutsideRoot",
    "Repeat",
    "SettingsApp",
    "Tab",
    "Track",
    "VideoApp",
    "ViewerApp",
    "available_apps",
    "create_app",
    "register",
]
