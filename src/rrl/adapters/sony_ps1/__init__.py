"""Identify and inspect PlayStation discs with a SYSTEM.CNF boot entry."""

from rrl.adapters.sony_ps2 import PlayStationAdapter


class PS1Adapter(PlayStationAdapter):
    id = "sony.ps1"
    name = "PlayStation 1"
    boot_key = "BOOT"
    device = "cdrom"
