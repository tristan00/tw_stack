"""Absolute paths. The only file allowed to hold one."""
import os

REPO = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
TWDATA = "D:/twdata"
RUNS_ROOT = TWDATA + "/runs/human"
WIKI_ROOT = TWDATA + "/wiki"
DATA_DIR = os.path.join(REPO, "data")
USER_SCRIPT = os.path.join(REPO, "USER_SCRIPT.json")
LOGS_DIR = TWDATA + "/logs/launcher"
REPLICATE_LOG = os.path.join(LOGS_DIR, "replicate.log")
