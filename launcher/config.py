import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(REPO))
import common

GAME_DIR = common.GAME_DIR
TWDATA = common.posix(common.TWDATA)
RUNS_ROOT = common.RUNS_ROOT
WIKI_ROOT = common.WIKI_ROOT
DATA_DIR = os.path.join(REPO, "data")
USER_SCRIPT = os.path.join(REPO, "USER_SCRIPT.json")
LOGS_DIR = common.LOGS_LAUNCHER
REPLICATE_LOG = os.path.join(LOGS_DIR, "replicate.log")
